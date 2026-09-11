"""Offline tests for run_turn's event translation + persistence.

A fake graph replaces LangGraph so we exercise our own event-translation and
storage code without any LLM/network. The fake emits LangGraph-shaped
astream_events; run_turn must turn them into the SSE events the UI consumes.
"""

from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY

from oli import agent
from oli.storage import Storage


class _FakeGraph:
    def __init__(self, events):
        self._events = events

    async def astream_events(self, _inputs, **_kwargs):
        for ev in self._events:
            yield ev


async def _collect(store, cid, message):
    out = []
    async for ev in agent.run_turn(store, cid, message):
        out.append(ev)
    return out


async def test_plain_answer_streams_tokens_and_persists(monkeypatch):
    events = [
        {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="Hello ")}},
        {"event": "on_chat_model_stream", "data": {"chunk": SimpleNamespace(content="there")}},
        {
            "event": "on_chat_model_end",
            "data": {"output": SimpleNamespace(content="Hello there", tool_calls=[])},
        },
    ]
    monkeypatch.setattr(agent, "get_graph", lambda: _FakeGraph(events))

    store = Storage()
    cid = await store.create_conversation()
    out = await _collect(store, cid, "hi")

    tokens = "".join(e["text"] for e in out if e["type"] == "token")
    done = next(e for e in out if e["type"] == "done")
    assert tokens == "Hello there"
    assert done["content"] == "Hello there"

    msgs = await store.get_messages(cid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["content"] == "Hello there"


async def test_tool_call_emits_tool_events_and_persists_tool_row(monkeypatch):
    events = [
        {"event": "on_tool_start", "name": "web_search", "data": {"input": {"query": "x"}}},
        {
            "event": "on_tool_end",
            "name": "web_search",
            "data": {"output": SimpleNamespace(content="search results...")},
        },
        {
            "event": "on_chat_model_end",
            "data": {"output": SimpleNamespace(content="Final answer", tool_calls=[])},
        },
    ]
    monkeypatch.setattr(agent, "get_graph", lambda: _FakeGraph(events))

    store = Storage()
    cid = await store.create_conversation()
    out = await _collect(store, cid, "search x")

    kinds = [e["type"] for e in out]
    assert "tool_start" in kinds and "tool_end" in kinds
    start = next(e for e in out if e["type"] == "tool_start")
    assert start["name"] == "web_search" and start["args"] == {"query": "x"}

    # A tool activity row is persisted for UI replay.
    roles = [m["role"] for m in await store.get_messages(cid)]
    assert roles == ["user", "tool", "assistant"]


async def test_classify_event_emits_intent_and_metric(monkeypatch):
    intent = {
        "category": "tools",
        "complexity": "hard",
        "needs_tools": True,
        "confidence": 0.8,
        "normalized_query": "search for x",
    }
    events = [
        {"event": "on_chain_end", "name": "classify", "data": {"output": {"intent": intent}}},
        {
            "event": "on_chat_model_end",
            "data": {"output": SimpleNamespace(content="Done", tool_calls=[])},
        },
    ]
    monkeypatch.setattr(agent, "get_graph", lambda: _FakeGraph(events))

    before = (
        REGISTRY.get_sample_value("oli_intents_total", {"category": "tools", "complexity": "hard"})
        or 0.0
    )

    store = Storage()
    cid = await store.create_conversation()
    out = await _collect(store, cid, "find x")

    intent_ev = next(e for e in out if e["type"] == "intent")
    assert intent_ev["intent"]["category"] == "tools"
    assert (
        REGISTRY.get_sample_value("oli_intents_total", {"category": "tools", "complexity": "hard"})
        == before + 1
    )


async def test_classifier_tokens_are_not_streamed_to_ui(monkeypatch):
    # A model stream event from the classify node must NOT reach the UI as a token.
    events = [
        {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "classify"},
            "data": {"chunk": SimpleNamespace(content='{"category":')},
        },
        {
            "event": "on_chat_model_stream",
            "metadata": {"langgraph_node": "agent"},
            "data": {"chunk": SimpleNamespace(content="Hello")},
        },
        {
            "event": "on_chat_model_end",
            "metadata": {"langgraph_node": "agent"},
            "data": {"output": SimpleNamespace(content="Hello", tool_calls=[])},
        },
    ]
    monkeypatch.setattr(agent, "get_graph", lambda: _FakeGraph(events))

    store = Storage()
    cid = await store.create_conversation()
    out = await _collect(store, cid, "hi")

    tokens = "".join(e["text"] for e in out if e["type"] == "token")
    assert tokens == "Hello"  # classifier JSON never leaked


async def test_run_once_raises_on_error(monkeypatch):
    events = [
        {
            "event": "on_chat_model_end",
            "data": {"output": SimpleNamespace(content="", tool_calls=[])},
        }
    ]

    async def boom(_inputs, **_kwargs):
        raise RuntimeError("model exploded")
        yield  # pragma: no cover

    fake = _FakeGraph(events)
    fake.astream_events = boom
    monkeypatch.setattr(agent, "get_graph", lambda: fake)

    store = Storage()
    cid = await store.create_conversation()
    with pytest.raises(RuntimeError, match="model exploded"):
        await agent.run_once(store, cid, "hi")
