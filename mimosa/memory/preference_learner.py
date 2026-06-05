"""Silent background preference learning for MimOSA (M5.2).

MimOSA gets more helpful over time by quietly noticing *patterns* in how the
user works — "PDFs are always opened in Okular", "music means Spotify",
"screenshots go to ~/Pictures" — and remembering them with a **confidence
score** so it can act on a habit only once it's reasonably sure.

This module implements that as a small, local SQLite table
(``learned_preferences``). It is deliberately:

* **Local-first & private.** A single on-device database
  (``~/.local/share/mimosa/preferences.db`` by default). No telemetry, nothing
  leaves the machine. Learning can be disabled wholesale.
* **Headless & dependency-free.** Standard-library :mod:`sqlite3` only — no
  GTK/audio/ML imports — so it loads and unit-tests on a headless machine.
* **Frequency-based & explainable.** Confidence for a ``(category, key) → value``
  is simply how dominant that value is among all observations for that key,
  scaled by how much evidence we have. There is no opaque model: every score is
  reproducible from the counts, and :meth:`PreferenceLearner.explain` shows the
  evidence.

Data model
----------
Each row is one observed *candidate value* for a *(category, key)* slot:

``category``  — the kind of preference (e.g. ``"file_open"``, ``"app_launch"``).
``key``       — the thing being decided about (e.g. ``"pdf"``, ``"music"``).
``value``     — the observed choice (e.g. ``"okular"``, ``"spotify"``).
``count``     — how many times this value was observed for the key.
``weight``    — accumulated weight (observations can be weighted, default 1.0).
``first_seen`` / ``last_seen`` — timestamps for recency/decay decisions.
"""

from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from mimosa.memory.paths import preferences_db_path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

#: Default minimum confidence before :meth:`PreferenceLearner.predict` will
#: surface a learned value as actionable.
DEFAULT_CONFIDENCE_THRESHOLD = 0.6

#: How many observations of the winning value are needed before its evidence
#: factor reaches 1.0 (below this, confidence is damped to avoid over-eager
#: conclusions from a single data point).
DEFAULT_EVIDENCE_SATURATION = 3


@dataclass
class LearnedPreference:
    """One learned ``(category, key) → value`` candidate with its evidence.

    Attributes:
        category: Preference category (e.g. ``"file_open"``).
        key: The decision slot (e.g. ``"pdf"``).
        value: The observed choice (e.g. ``"okular"``).
        count: Number of observations of this value for the key.
        weight: Accumulated (possibly weighted) evidence for this value.
        confidence: Dominance of this value among all values for the key,
            damped by total evidence. In ``[0, 1]``.
        first_seen: Unix epoch seconds of the first observation.
        last_seen: Unix epoch seconds of the most recent observation.
    """

    category: str
    key: str
    value: str
    count: int
    weight: float
    confidence: float
    first_seen: float
    last_seen: float

    def to_dict(self) -> Dict:
        return {
            "category": self.category,
            "key": self.key,
            "value": self.value,
            "count": self.count,
            "weight": self.weight,
            "confidence": self.confidence,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


class PreferenceLearner:
    """Learns and recalls user preferences from observed behaviour.

    Args:
        db_path: SQLite path. ``None`` uses the default under the user data
            dir; ``":memory:"`` keeps everything in RAM (tests).
        enabled: When ``False``, :meth:`observe` is a no-op (the user has
            opted out of background learning) while reads still work.
        confidence_threshold: Default cut-off used by :meth:`predict`.
        evidence_saturation: Observation count at which a value's evidence
            factor reaches 1.0.
    """

    def __init__(
        self,
        db_path: Optional[Union[str, Path]] = None,
        *,
        enabled: bool = True,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        evidence_saturation: int = DEFAULT_EVIDENCE_SATURATION,
    ) -> None:
        if db_path is None:
            db_path = preferences_db_path()
        self._is_memory = str(db_path) == ":memory:"
        self.db_path = str(db_path)
        self.enabled = bool(enabled)
        self.confidence_threshold = float(confidence_threshold)
        self.evidence_saturation = max(1, int(evidence_saturation))
        self._lock = threading.RLock()

        if not self._is_memory:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    # -- schema ------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS learned_preferences (
                    category    TEXT NOT NULL,
                    key         TEXT NOT NULL,
                    value       TEXT NOT NULL,
                    count       INTEGER NOT NULL DEFAULT 0,
                    weight      REAL NOT NULL DEFAULT 0.0,
                    first_seen  REAL NOT NULL,
                    last_seen   REAL NOT NULL,
                    PRIMARY KEY (category, key, value)
                );

                CREATE INDEX IF NOT EXISTS idx_pref_catkey
                    ON learned_preferences(category, key);
                """
            )
            self._conn.commit()
            current = cur.execute("PRAGMA user_version").fetchone()[0]
            if current < SCHEMA_VERSION:
                cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self._conn.commit()

    # -- learning ----------------------------------------------------------

    def observe(
        self,
        category: str,
        key: str,
        value: str,
        *,
        weight: float = 1.0,
        timestamp: Optional[float] = None,
    ) -> None:
        """Record one observation of ``value`` chosen for ``(category, key)``.

        A no-op when learning is disabled or any field is blank. Increments the
        candidate's count and accumulated weight, updating ``last_seen``.
        """
        if not self.enabled:
            return
        category = (category or "").strip()
        key = (key or "").strip()
        value = (value or "").strip()
        if not (category and key and value):
            return
        try:
            w = float(weight)
        except (TypeError, ValueError):
            w = 1.0
        if w <= 0:
            return
        ts = time.time() if timestamp is None else float(timestamp)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO learned_preferences
                    (category, key, value, count, weight, first_seen, last_seen)
                VALUES (?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(category, key, value) DO UPDATE SET
                    count = count + 1,
                    weight = weight + excluded.weight,
                    last_seen = excluded.last_seen
                """,
                (category, key, value, w, ts, ts),
            )
            self._conn.commit()

    # -- recall ------------------------------------------------------------

    def _candidates(self, category: str, key: str) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM learned_preferences WHERE category = ? AND key = ?",
                (category, key),
            ).fetchall()

    def _confidence(self, winning_weight: float, total_weight: float,
                    winning_count: int) -> float:
        """Dominance of the winner, damped by how much evidence exists.

        ``dominance`` is the winner's share of total weight for the key.
        ``evidence`` ramps from 0→1 as observations approach the saturation
        count, so a single observation can't yield full confidence even at 100%
        dominance.
        """
        if total_weight <= 0:
            return 0.0
        dominance = winning_weight / total_weight
        evidence = 1.0 - math.exp(-winning_count / self.evidence_saturation)
        return round(max(0.0, min(1.0, dominance * evidence)), 4)

    def get_preferences(self, category: str, key: str) -> List[LearnedPreference]:
        """Return all candidate values for ``(category, key)``, best first."""
        rows = self._candidates((category or "").strip(), (key or "").strip())
        total_weight = sum(r["weight"] for r in rows)
        prefs = [
            LearnedPreference(
                category=r["category"],
                key=r["key"],
                value=r["value"],
                count=r["count"],
                weight=r["weight"],
                confidence=self._confidence(r["weight"], total_weight, r["count"]),
                first_seen=r["first_seen"],
                last_seen=r["last_seen"],
            )
            for r in rows
        ]
        prefs.sort(key=lambda p: (p.confidence, p.weight, p.last_seen), reverse=True)
        return prefs

    def predict(
        self,
        category: str,
        key: str,
        *,
        threshold: Optional[float] = None,
    ) -> Optional[LearnedPreference]:
        """Return the best learned value if confident enough, else ``None``.

        ``threshold`` overrides :attr:`confidence_threshold` for this call.
        """
        cut = self.confidence_threshold if threshold is None else float(threshold)
        prefs = self.get_preferences(category, key)
        if prefs and prefs[0].confidence >= cut:
            return prefs[0]
        return None

    def predict_value(
        self,
        category: str,
        key: str,
        *,
        threshold: Optional[float] = None,
        default: Optional[str] = None,
    ) -> Optional[str]:
        """Convenience wrapper around :meth:`predict` returning just the value."""
        pref = self.predict(category, key, threshold=threshold)
        return pref.value if pref is not None else default

    def explain(self, category: str, key: str) -> str:
        """Return a short human-readable explanation of the evidence."""
        prefs = self.get_preferences(category, key)
        if not prefs:
            return f"No preference learned for {category}/{key} yet."
        lines = [f"Preference for {category}/{key}:"]
        for p in prefs:
            lines.append(
                f"  • {p.value}: {p.count} obs, confidence {p.confidence:.0%}"
            )
        return "\n".join(lines)

    # -- introspection / maintenance --------------------------------------

    def categories(self) -> List[str]:
        """Distinct categories that have at least one observation."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT category FROM learned_preferences ORDER BY category"
            ).fetchall()
        return [r["category"] for r in rows]

    def keys(self, category: str) -> List[str]:
        """Distinct keys observed within a category."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT key FROM learned_preferences WHERE category = ? "
                "ORDER BY key",
                ((category or "").strip(),),
            ).fetchall()
        return [r["key"] for r in rows]

    def all_preferences(
        self, *, min_confidence: float = 0.0
    ) -> List[LearnedPreference]:
        """Return every learned preference, optionally filtered by confidence.

        The best candidate per ``(category, key)`` is what's compared against
        ``min_confidence``; all qualifying candidates are returned, best first.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT category, key FROM learned_preferences"
            ).fetchall()
        out: List[LearnedPreference] = []
        for r in rows:
            prefs = self.get_preferences(r["category"], r["key"])
            if prefs and prefs[0].confidence >= min_confidence:
                out.extend(prefs)
        out.sort(key=lambda p: (p.confidence, p.weight), reverse=True)
        return out

    def forget(self, category: str, key: Optional[str] = None,
               value: Optional[str] = None) -> int:
        """Delete learned rows. Returns the number removed.

        * ``forget(cat)`` — drop the whole category.
        * ``forget(cat, key)`` — drop one key.
        * ``forget(cat, key, value)`` — drop a single candidate value.
        """
        category = (category or "").strip()
        clauses = ["category = ?"]
        params: List = [category]
        if key is not None:
            clauses.append("key = ?")
            params.append((key or "").strip())
        if value is not None:
            clauses.append("value = ?")
            params.append((value or "").strip())
        sql = "DELETE FROM learned_preferences WHERE " + " AND ".join(clauses)
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur.rowcount or 0

    def clear_all(self) -> None:
        """Forget every learned preference (factory reset)."""
        with self._lock:
            self._conn.execute("DELETE FROM learned_preferences")
            self._conn.commit()

    def close(self) -> None:
        """Close the underlying connection. Safe to call more than once."""
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

    def __enter__(self) -> "PreferenceLearner":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        loc = ":memory:" if self._is_memory else self.db_path
        return f"PreferenceLearner(path={loc!r}, enabled={self.enabled})"
