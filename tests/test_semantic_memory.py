"""Tests for semantic memory (M5.3 — local embeddings & recall).

These exercise the dependency-free fallback path (hashing embedder +
in-process vector store) so they run hermetically on a headless CI box whether
or not Chroma / sentence-transformers happen to be installed.
"""

from __future__ import annotations

import pytest

from mimosa.memory.semantic_memory import (
    HashingEmbedder,
    SemanticMemory,
    SemanticResult,
    _cosine,
    _FallbackVectorStore,
)


@pytest.fixture
def mem():
    # Force the fallback backend for determinism/hermeticity.
    m = SemanticMemory(use_chroma=False)
    yield m
    m.close()


# -- hashing embedder ------------------------------------------------------


def test_embedder_deterministic():
    e = HashingEmbedder(dim=64)
    assert e.embed("hello world") == e.embed("hello world")


def test_embedder_dimension():
    e = HashingEmbedder(dim=128)
    assert len(e.embed("anything here")) == 128


def test_embedder_normalised():
    e = HashingEmbedder(dim=64)
    vec = e.embed("some tokens here please")
    norm = sum(v * v for v in vec) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_embedder_empty_text_zero_vector():
    e = HashingEmbedder(dim=32)
    assert e.embed("") == [0.0] * 32


def test_embedder_min_dim_enforced():
    assert HashingEmbedder(dim=1).dim >= 8


def test_embedder_callable():
    e = HashingEmbedder()
    assert e("word") == e.embed("word")


# -- cosine ----------------------------------------------------------------


def test_cosine_identical_is_one():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_handles_empty_and_mismatch():
    assert _cosine([], [1.0]) == 0.0
    assert _cosine([1.0, 2.0], [1.0]) == 0.0
    assert _cosine([0.0, 0.0], [0.0, 0.0]) == 0.0


# -- add / query -----------------------------------------------------------


def test_add_returns_doc_id(mem):
    doc_id = mem.add("hello there")
    assert isinstance(doc_id, str) and doc_id
    assert mem.count() == 1


def test_add_blank_returns_none(mem):
    assert mem.add("   ") is None
    assert mem.count() == 0


def test_add_custom_doc_id(mem):
    mem.add("hello", doc_id="fixed")
    assert mem.delete("fixed") is True


def test_query_empty_store_returns_empty(mem):
    assert mem.query("anything") == []


def test_query_finds_similar(mem):
    mem.add("the quick brown fox jumps over the lazy dog")
    mem.add("python is a great programming language")
    results = mem.query("quick brown fox", n_results=1)
    assert results
    assert "fox" in results[0].text


def test_query_respects_n_results(mem):
    for i in range(5):
        mem.add(f"document number {i} about cats")
    results = mem.query("cats", n_results=2)
    assert len(results) <= 2


def test_query_min_score_filters(mem):
    mem.add("completely unrelated topic about astronomy")
    results = mem.query("banana smoothie recipe", min_score=0.9)
    assert results == []


def test_query_results_sorted_descending(mem):
    mem.add("apple banana cherry")
    mem.add("apple banana")
    mem.add("apple")
    results = mem.query("apple banana cherry", n_results=3)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_results_are_semantic_result(mem):
    mem.add("hello world")
    r = mem.query("hello", n_results=1)[0]
    assert isinstance(r, SemanticResult)
    assert 0.0 <= r.score <= 1.0


# -- add_turn / recall -----------------------------------------------------


def test_add_turn_combines_sides(mem):
    mem.add_turn("what's the weather", "it is sunny", session_id="s1", intent="weather")
    r = mem.query("weather sunny", n_results=1)[0]
    assert "User:" in r.text and "MimOSA:" in r.text
    assert r.metadata.get("session_id") == "s1"


def test_add_turn_empty_returns_none(mem):
    assert mem.add_turn("", "") is None


def test_add_turn_user_only(mem):
    doc_id = mem.add_turn("just a question", "")
    assert doc_id is not None


def test_recall_above_threshold(mem):
    mem.add("we discussed hiking trails in the mountains")
    hit = mem.recall("hiking trails mountains", threshold=0.2)
    assert hit is not None


def test_recall_below_threshold_returns_none(mem):
    mem.add("we discussed hiking trails")
    assert mem.recall("quantum chromodynamics lecture", threshold=0.5) is None


def test_metadata_timestamp_added(mem):
    mem.add("hello")
    r = mem.query("hello", n_results=1)[0]
    assert "timestamp" in r.metadata


# -- maintenance -----------------------------------------------------------


def test_delete(mem):
    doc_id = mem.add("temporary note")
    assert mem.delete(doc_id) is True
    assert mem.count() == 0


def test_delete_unknown_returns_false(mem):
    assert mem.delete("nonexistent") is False


def test_reset(mem):
    mem.add("a")
    mem.add("b")
    mem.reset()
    assert mem.count() == 0


# -- backend / embedder selection -----------------------------------------


def test_uses_fallback_embedder_by_default(mem):
    assert mem.uses_fallback_embedder is True
    assert mem.backend == "fallback"


def test_injected_embedder_used():
    calls = []

    def embedder(text):
        calls.append(text)
        return [1.0, 0.0, 0.0]

    m = SemanticMemory(use_chroma=False, embedder=embedder)
    m.add("hello")
    assert calls  # embedder was invoked
    assert not m.uses_fallback_embedder
    m.close()


def test_repr_mentions_backend(mem):
    assert "fallback" in repr(mem)


# -- persistence (fallback jsonl) -----------------------------------------


def test_fallback_persists_to_disk(tmp_path):
    m1 = SemanticMemory(persist_dir=tmp_path, use_chroma=False)
    m1.add("durable memory snippet about gardening")
    m1.close()

    m2 = SemanticMemory(persist_dir=tmp_path, use_chroma=False)
    assert m2.count() == 1
    assert m2.recall("gardening snippet", threshold=0.1) is not None
    m2.close()


def test_fallback_store_corrupt_file_starts_empty(tmp_path):
    bad = tmp_path / "mimosa_memory.jsonl"
    bad.write_text("not valid json\n{also bad")
    store = _FallbackVectorStore(persist_path=bad)
    assert store.count() == 0


def test_context_manager(tmp_path):
    with SemanticMemory(persist_dir=tmp_path, use_chroma=False) as m:
        m.add("scoped note")
        assert m.count() == 1


def test_semantic_result_to_dict():
    r = SemanticResult("text", 0.5, {"k": "v"}, "id1")
    d = r.to_dict()
    assert d["score"] == 0.5 and d["doc_id"] == "id1"
