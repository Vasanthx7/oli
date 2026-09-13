"""Offline tests for intent classification (no network — classifier is faked)."""

from oli import intent as intent_mod
from oli.intent import Intent


class _FakeClassifier:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc

    async def ainvoke(self, _messages):
        if self._exc is not None:
            raise self._exc
        return self._result


async def test_classify_returns_structured_intent(monkeypatch):
    want = Intent(
        category="tools",
        complexity="hard",
        needs_tools=True,
        confidence=0.9,
        normalized_query="search for x",
    )
    monkeypatch.setattr(intent_mod, "get_classifier", lambda: _FakeClassifier(result=want))

    got = await intent_mod.classify("find x for me")
    assert got == want


async def test_classify_falls_back_to_chat_on_error(monkeypatch):
    monkeypatch.setattr(
        intent_mod, "get_classifier", lambda: _FakeClassifier(exc=RuntimeError("endpoint down"))
    )

    got = await intent_mod.classify("hello there")
    # Safe default: simple chat, no tools, zero confidence, query preserved.
    assert got.category == "chat"
    assert got.complexity == "simple"
    assert got.needs_tools is False
    assert got.confidence == 0.0
    assert got.normalized_query == "hello there"


async def test_classify_falls_back_on_unexpected_type(monkeypatch):
    monkeypatch.setattr(
        intent_mod, "get_classifier", lambda: _FakeClassifier(result={"not": "an intent"})
    )

    got = await intent_mod.classify("hello")
    assert got.category == "chat" and got.confidence == 0.0
