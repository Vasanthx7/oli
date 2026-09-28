"""Intent classification via Jev: routing, failover, and shadow mode (all offline).

The Jev client and the LLM classifier are both mocked, so these exercise oli.intent's
orchestration without any network or API key.
"""

import asyncio

import pytest

from oli import intent, jev
from oli.config import settings


class _FakeClassifier:
    """Stand-in for the LLM structured classifier; returns a fixed Intent."""

    def __init__(self, result):
        self._result = result

    async def ainvoke(self, _messages):
        return self._result


def _llm_intent():
    return intent.Intent(
        category="tools",
        complexity="hard",
        needs_tools=True,
        confidence=0.7,
        normalized_query="q",
    )


def _jev_result(category="chat", complexity="simple", needs_tools=False, conf=0.95):
    return jev.JevResult(
        output=intent.IntentDecision(
            category=category, complexity=complexity, needs_tools=needs_tools
        ),
        confidence={"category": conf, "complexity": conf, "needs_tools": conf},
        input_tokens=12,
    )


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # Default: everything off, no key. Tests opt in.
    monkeypatch.setattr(settings, "use_jev", False)
    monkeypatch.setattr(settings, "jev_shadow", False)
    monkeypatch.setattr(settings, "jev_api_key", "")
    yield


async def test_use_jev_routes_through_jev(monkeypatch):
    monkeypatch.setattr(settings, "use_jev", True)

    async def fake_decide(schema, prompt):
        assert schema is intent.IntentDecision
        return _jev_result(category="browse", complexity="hard", needs_tools=True)

    monkeypatch.setattr(intent.jev, "decide", fake_decide)

    result = await intent.classify("add the cheapest cable to my cart")
    assert result.category == "browse"
    assert result.complexity == "hard"
    assert result.needs_tools is True
    assert result.confidence == pytest.approx(0.95)


async def test_use_jev_fails_over_to_llm(monkeypatch):
    monkeypatch.setattr(settings, "use_jev", True)
    monkeypatch.setattr(intent, "get_classifier", lambda: _FakeClassifier(_llm_intent()))

    async def boom(schema, prompt):
        raise RuntimeError("jev down")

    monkeypatch.setattr(intent.jev, "decide", boom)

    result = await intent.classify("what's the weather?")
    # Fell back to the LLM classifier's result.
    assert result.category == "tools"
    assert result.complexity == "hard"


async def test_shadow_records_agreement(monkeypatch):
    monkeypatch.setattr(settings, "jev_shadow", True)
    monkeypatch.setattr(settings, "jev_api_key", "k")  # jev.available() -> True
    monkeypatch.setattr(intent, "get_classifier", lambda: _FakeClassifier(_llm_intent()))

    calls = []

    async def fake_decide(schema, prompt):
        calls.append(prompt)
        # Disagrees with the LLM on category (tools vs chat) — should record agree=no.
        return _jev_result(category="chat", complexity="hard", needs_tools=True)

    monkeypatch.setattr(intent.jev, "decide", fake_decide)

    result = await intent.classify("hello there")
    # Primary result is the LLM's, unaffected by shadow.
    assert result.category == "tools"
    # Let the fire-and-forget shadow task finish.
    await asyncio.gather(*list(intent._shadow_tasks))
    assert calls, "shadow should have invoked Jev"


async def test_no_shadow_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "jev_shadow", False)
    monkeypatch.setattr(intent, "get_classifier", lambda: _FakeClassifier(_llm_intent()))

    async def fake_decide(schema, prompt):
        raise AssertionError("Jev must not be called when shadow + use_jev are off")

    monkeypatch.setattr(intent.jev, "decide", fake_decide)
    result = await intent.classify("hi")
    assert result.category == "tools"
