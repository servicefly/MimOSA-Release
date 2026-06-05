"""Semantic (vector) memory for MimOSA (M5.3 — long-term recall).

Lets MimOSA answer "have we discussed this before?" by storing past
conversation snippets as vector embeddings and retrieving the most *meaning*-
similar ones to feed back into the LLM context — all **on-device**.

Graceful degradation is a first-class requirement
--------------------------------------------------
The "good" backend is **Chroma** (a local vector DB) with
**sentence-transformers** producing embeddings. Both are heavy, optional
dependencies that are frequently absent on a headless CI box or a minimal
install. So this module is built in three independent layers, each of which can
be missing without breaking the others:

1. **Vector store** — Chroma if importable; otherwise a pure-Python in-process
   store (:class:`_FallbackVectorStore`) that does brute-force cosine search.
2. **Embedder** — an injected callable, else sentence-transformers if
   importable, else a deterministic, dependency-free hashing embedder
   (:class:`HashingEmbedder`) so semantic-ish recall still works offline and
   tests stay hermetic.
3. **Persistence** — a directory on disk if given and supported, else memory.

The public API (:class:`SemanticMemory`) is identical regardless of which
layers are active, so callers never branch on availability. :data:`HAS_CHROMA`
and :data:`HAS_SENTENCE_TRANSFORMERS` are exposed for diagnostics/tests.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Union

from mimosa.memory.paths import semantic_store_dir

logger = logging.getLogger(__name__)

# --- optional dependency probes (never raise at import) --------------------
try:  # pragma: no cover - presence depends on the host
    import chromadb  # type: ignore

    HAS_CHROMA = True
except Exception:  # pragma: no cover
    chromadb = None  # type: ignore
    HAS_CHROMA = False

try:  # pragma: no cover - presence depends on the host
    from sentence_transformers import SentenceTransformer  # type: ignore

    HAS_SENTENCE_TRANSFORMERS = True
except Exception:  # pragma: no cover
    SentenceTransformer = None  # type: ignore
    HAS_SENTENCE_TRANSFORMERS = False


#: Default embedding dimensionality for the fallback hashing embedder. Small
#: enough to be cheap, large enough to keep token collisions rare.
DEFAULT_FALLBACK_DIM = 256

#: Default sentence-transformers model (small, CPU-friendly, fully local).
DEFAULT_ST_MODEL = "all-MiniLM-L6-v2"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


@dataclass
class SemanticResult:
    """One retrieved memory with its similarity score.

    Attributes:
        text: The stored snippet.
        score: Cosine similarity in ``[0, 1]`` (higher = more similar).
        metadata: Arbitrary metadata stored alongside the snippet.
        doc_id: Stable identifier of the stored document.
    """

    text: str
    score: float
    metadata: Dict = field(default_factory=dict)
    doc_id: str = ""

    def to_dict(self) -> Dict:
        return {
            "text": self.text,
            "score": self.score,
            "metadata": self.metadata,
            "doc_id": self.doc_id,
        }


# ---------------------------------------------------------------------------
# Embedders
# ---------------------------------------------------------------------------


class HashingEmbedder:
    """Deterministic, dependency-free embedder (graceful fallback).

    Hashes each token into one of ``dim`` buckets and accumulates an L2-
    normalised bag-of-tokens vector. This is *not* a learned semantic model,
    but it is fully offline, deterministic (great for tests), and gives useful
    lexical-overlap similarity so recall degrades gracefully rather than
    disappearing when sentence-transformers is unavailable.
    """

    def __init__(self, dim: int = DEFAULT_FALLBACK_DIM) -> None:
        self.dim = max(8, int(dim))

    def _bucket(self, token: str) -> int:
        h = hashlib.sha1(token.encode("utf-8")).digest()
        return int.from_bytes(h[:4], "big") % self.dim

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in _tokenize(text):
            vec[self._bucket(tok)] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def __call__(self, text: str) -> List[float]:
        return self.embed(text)


class SentenceTransformerEmbedder:  # pragma: no cover - requires heavy dep
    """Wraps a local sentence-transformers model into an embed callable.

    Loaded lazily on first use so importing this module never pulls PyTorch.
    Falls back is handled by the caller (:class:`SemanticMemory`) if the model
    cannot be loaded.
    """

    def __init__(self, model_name: str = DEFAULT_ST_MODEL) -> None:
        if not HAS_SENTENCE_TRANSFORMERS:
            raise RuntimeError("sentence-transformers is not installed")
        self.model_name = model_name
        self._model = None

    def _ensure(self):
        if self._model is None:
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, text: str) -> List[float]:
        model = self._ensure()
        vec = model.encode(text, normalize_embeddings=True)
        return [float(x) for x in vec]

    def __call__(self, text: str) -> List[float]:
        return self.embed(text)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    # Clamp to [0,1]: embeddings here are non-negative (fallback) or normalised;
    # negative cosines (possible with ST) are floored to 0 for an intuitive score.
    return max(0.0, min(1.0, dot / (na * nb)))


# ---------------------------------------------------------------------------
# Fallback vector store (used when Chroma is unavailable)
# ---------------------------------------------------------------------------


class _FallbackVectorStore:
    """A tiny in-process vector store with brute-force cosine search.

    Optionally persists to a JSON-lines file so recall survives restarts even
    without Chroma. Thread-safe via the owning :class:`SemanticMemory`'s lock.
    """

    def __init__(self, persist_path: Optional[Path] = None) -> None:
        self.persist_path = persist_path
        self._docs: Dict[str, Dict] = {}
        self._load()

    def _load(self) -> None:
        if not self.persist_path or not self.persist_path.exists():
            return
        try:
            import json

            with open(self.persist_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rec = json.loads(line)
                        self._docs[rec["doc_id"]] = rec
        except Exception:  # pragma: no cover - corrupt file => start fresh
            logger.warning("Could not load semantic fallback store; starting empty")
            self._docs = {}

    def _save(self) -> None:
        if not self.persist_path:
            return
        try:
            import json

            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.persist_path.with_suffix(self.persist_path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                for rec in self._docs.values():
                    fh.write(json.dumps(rec) + "\n")
            tmp.replace(self.persist_path)
        except Exception:  # pragma: no cover - persistence is best-effort
            logger.warning("Could not persist semantic fallback store")

    def upsert(self, doc_id: str, text: str, embedding: List[float],
               metadata: Dict) -> None:
        self._docs[doc_id] = {
            "doc_id": doc_id,
            "text": text,
            "embedding": embedding,
            "metadata": metadata,
        }
        self._save()

    def delete(self, doc_id: str) -> bool:
        existed = self._docs.pop(doc_id, None) is not None
        if existed:
            self._save()
        return existed

    def query(self, embedding: List[float], n_results: int) -> List[SemanticResult]:
        scored = [
            SemanticResult(
                text=rec["text"],
                score=_cosine(embedding, rec["embedding"]),
                metadata=rec.get("metadata", {}),
                doc_id=rec["doc_id"],
            )
            for rec in self._docs.values()
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:n_results]

    def count(self) -> int:
        return len(self._docs)

    def reset(self) -> None:
        self._docs.clear()
        self._save()


# ---------------------------------------------------------------------------
# Public façade
# ---------------------------------------------------------------------------


class SemanticMemory:
    """On-device semantic memory with graceful degradation.

    Args:
        persist_dir: Directory for durable storage. ``None`` keeps everything
            in memory (tests / ephemeral use). With Chroma present this becomes
            a persistent Chroma client; otherwise a JSON-lines fallback file is
            written here.
        embedder: A callable ``str -> List[float]``. If ``None``, a
            sentence-transformers embedder is used when available, else the
            deterministic :class:`HashingEmbedder` fallback.
        collection_name: Logical collection/namespace name.
        use_chroma: Force-disable Chroma by passing ``False`` (used by tests to
            exercise the fallback path even when Chroma is installed).
        fallback_dim: Dimensionality for the hashing fallback embedder.
    """

    def __init__(
        self,
        persist_dir: Optional[Union[str, Path]] = None,
        *,
        embedder: Optional[Callable[[str], List[float]]] = None,
        collection_name: str = "mimosa_memory",
        use_chroma: Optional[bool] = None,
        fallback_dim: int = DEFAULT_FALLBACK_DIM,
    ) -> None:
        self.collection_name = collection_name
        self._lock = threading.RLock()
        self.persist_dir = Path(persist_dir).expanduser() if persist_dir else None

        # -- resolve embedder ------------------------------------------------
        self._injected_embedder = embedder is not None
        self.embedder = embedder or self._default_embedder(fallback_dim)
        self.uses_fallback_embedder = isinstance(self.embedder, HashingEmbedder)

        # -- resolve backend -------------------------------------------------
        want_chroma = HAS_CHROMA if use_chroma is None else (use_chroma and HAS_CHROMA)
        self._chroma_collection = None
        self._fallback: Optional[_FallbackVectorStore] = None
        self.backend = "fallback"
        if want_chroma:
            if self._init_chroma():
                self.backend = "chroma"
            else:  # pragma: no cover - chroma init failure path
                self._init_fallback()
        else:
            self._init_fallback()

    # -- backend init ------------------------------------------------------

    @staticmethod
    def _default_embedder(fallback_dim: int) -> Callable[[str], List[float]]:
        if HAS_SENTENCE_TRANSFORMERS:  # pragma: no cover - heavy dep
            try:
                return SentenceTransformerEmbedder()
            except Exception:
                logger.warning("sentence-transformers load failed; using fallback")
        return HashingEmbedder(dim=fallback_dim)

    def _init_chroma(self) -> bool:  # pragma: no cover - requires chromadb
        try:
            if self.persist_dir is not None:
                self.persist_dir.mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=str(self.persist_dir))
            else:
                client = chromadb.EphemeralClient()
            # We supply our own embeddings, so no embedding_function here.
            self._chroma_collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            return True
        except Exception:
            logger.exception("Chroma init failed; falling back to in-process store")
            self._chroma_collection = None
            return False

    def _init_fallback(self) -> None:
        persist_path = None
        if self.persist_dir is not None:
            persist_path = self.persist_dir / f"{self.collection_name}.jsonl"
        self._fallback = _FallbackVectorStore(persist_path=persist_path)

    # -- writing -----------------------------------------------------------

    def add(
        self,
        text: str,
        *,
        metadata: Optional[Dict] = None,
        doc_id: Optional[str] = None,
    ) -> Optional[str]:
        """Embed and store a snippet. Returns its ``doc_id`` (or ``None`` if
        the text was blank)."""
        text = (text or "").strip()
        if not text:
            return None
        doc_id = doc_id or uuid.uuid4().hex
        meta = dict(metadata or {})
        meta.setdefault("timestamp", time.time())
        embedding = list(self.embedder(text))
        with self._lock:
            if self.backend == "chroma":  # pragma: no cover - requires chromadb
                self._chroma_collection.upsert(
                    ids=[doc_id],
                    embeddings=[embedding],
                    documents=[text],
                    metadatas=[self._clean_meta(meta)],
                )
            else:
                self._fallback.upsert(doc_id, text, embedding, meta)
        return doc_id

    def add_turn(
        self,
        user_text: str,
        assistant_text: str = "",
        *,
        session_id: Optional[str] = None,
        intent: Optional[str] = None,
        doc_id: Optional[str] = None,
    ) -> Optional[str]:
        """Store a conversation turn as a single retrievable snippet.

        The user and assistant text are combined so a later query can recall
        the whole exchange. Useful for "have we discussed this before?".
        """
        parts = []
        if user_text and user_text.strip():
            parts.append(f"User: {user_text.strip()}")
        if assistant_text and assistant_text.strip():
            parts.append(f"MimOSA: {assistant_text.strip()}")
        if not parts:
            return None
        meta = {"kind": "turn"}
        if session_id:
            meta["session_id"] = session_id
        if intent:
            meta["intent"] = intent
        return self.add("\n".join(parts), metadata=meta, doc_id=doc_id)

    @staticmethod
    def _clean_meta(meta: Dict) -> Dict:  # pragma: no cover - chroma-only
        """Chroma only accepts str/int/float/bool metadata values."""
        clean: Dict = {}
        for k, v in meta.items():
            if isinstance(v, (str, int, float, bool)):
                clean[str(k)] = v
            else:
                clean[str(k)] = str(v)
        return clean or {"_": ""}

    # -- reading -----------------------------------------------------------

    def query(
        self,
        text: str,
        *,
        n_results: int = 5,
        min_score: float = 0.0,
    ) -> List[SemanticResult]:
        """Return the most semantically-similar stored snippets.

        Args:
            text: The query text.
            n_results: Maximum number of results.
            min_score: Drop results below this cosine similarity.
        """
        text = (text or "").strip()
        if not text or self.count() == 0:
            return []
        n_results = max(1, int(n_results))
        embedding = list(self.embedder(text))
        with self._lock:
            if self.backend == "chroma":  # pragma: no cover - requires chromadb
                res = self._chroma_collection.query(
                    query_embeddings=[embedding],
                    n_results=min(n_results, self.count()),
                )
                results = self._parse_chroma(res)
            else:
                results = self._fallback.query(embedding, n_results)
        return [r for r in results if r.score >= min_score][:n_results]

    def recall(self, text: str, *, threshold: float = 0.5) -> Optional[SemanticResult]:
        """Return the single best match above ``threshold``, else ``None``.

        Handy for the "have we talked about this before?" check.
        """
        hits = self.query(text, n_results=1, min_score=threshold)
        return hits[0] if hits else None

    @staticmethod
    def _parse_chroma(res: Dict) -> List[SemanticResult]:  # pragma: no cover
        out: List[SemanticResult] = []
        ids = (res.get("ids") or [[]])[0]
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for i, doc in enumerate(docs):
            dist = dists[i] if i < len(dists) else 0.0
            # cosine distance -> similarity
            score = max(0.0, min(1.0, 1.0 - float(dist)))
            out.append(
                SemanticResult(
                    text=doc,
                    score=score,
                    metadata=metas[i] if i < len(metas) else {},
                    doc_id=ids[i] if i < len(ids) else "",
                )
            )
        return out

    # -- maintenance -------------------------------------------------------

    def count(self) -> int:
        """Number of stored snippets."""
        with self._lock:
            if self.backend == "chroma":  # pragma: no cover - requires chromadb
                try:
                    return int(self._chroma_collection.count())
                except Exception:
                    return 0
            return self._fallback.count()

    def delete(self, doc_id: str) -> bool:
        """Delete a snippet by id. Returns ``True`` if one was removed."""
        with self._lock:
            if self.backend == "chroma":  # pragma: no cover - requires chromadb
                try:
                    self._chroma_collection.delete(ids=[doc_id])
                    return True
                except Exception:
                    return False
            return self._fallback.delete(doc_id)

    def reset(self) -> None:
        """Remove all stored snippets."""
        with self._lock:
            if self.backend == "chroma":  # pragma: no cover - requires chromadb
                try:
                    ids = self._chroma_collection.get().get("ids", [])
                    if ids:
                        self._chroma_collection.delete(ids=ids)
                except Exception:
                    logger.warning("Chroma reset failed")
            else:
                self._fallback.reset()

    def close(self) -> None:  # pragma: no cover - trivial
        """Release resources (no-op for in-process fallback)."""
        # Chroma persists automatically; nothing to flush for the fallback.
        return None

    def __enter__(self) -> "SemanticMemory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"SemanticMemory(backend={self.backend!r}, "
            f"embedder={'fallback' if self.uses_fallback_embedder else 'model'}, "
            f"count={self.count()})"
        )
