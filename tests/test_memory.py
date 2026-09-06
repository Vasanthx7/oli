"""Memory tests use the real (local, cached) fastembed model — no network."""

from oli.memory import MemoryStore, _parse_facts


def test_remember_and_dedupe(storage):
    ms = MemoryStore(storage)
    assert ms.remember("The user has a golden retriever named Max")["stored"] is True
    dup = ms.remember("The user owns a golden retriever called Max")
    assert dup["stored"] is False
    assert dup["reason"] == "duplicate"


def test_recall_relevance_and_threshold(storage):
    ms = MemoryStore(storage)
    ms.remember("The user prefers Python for backend work")
    ms.remember("The user is building a personal AI assistant")

    hits = ms.recall("which programming language do I like?")
    assert hits, "expected a relevant memory"
    assert "Python" in hits[0]["content"]

    # Unrelated query should return nothing above threshold.
    assert ms.recall("what is the capital of France?") == []


def test_parse_facts_shapes():
    assert _parse_facts('["a", "b"]') == ["a", "b"]
    assert _parse_facts('```json\n["a"]\n```') == ["a"]
    assert _parse_facts("prose ['ignored'] not json") == []
    assert _parse_facts("[]") == []
    assert _parse_facts('["ok", 123, "", "  ", "two"]') == ["ok", "two"]
