"""The /metrics endpoint exposes Prometheus metrics and reflects request +
agent activity (HTTP counts, LLM tokens/latency, and agent errors)."""

from types import SimpleNamespace

from prometheus_client import REGISTRY

from oli import agent
from oli.storage import Storage


class _FakeGraph:
    def __init__(self, events):
        self._events = events

    async def astream_events(self, _inputs, **_kwargs):
        for ev in self._events:
            yield ev


def _sample(name: str, labels: dict) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_metrics_endpoint_exposes_prometheus(client):
    # Generate some request activity first.
    await client.get("/api/conversations")

    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    # Our custom metric families are all present.
    for family in (
        "oli_http_requests_total",
        "oli_chat_turns_total",
        "oli_tool_calls_total",
        "oli_llm_tokens_total",
        "oli_llm_call_duration_seconds",
        "oli_agent_errors_total",
    ):
        assert family in body


async def test_llm_token_and_latency_metrics_recorded(monkeypatch):
    # A chat-model call with usage metadata and a matching start/end run_id.
    events = [
        {
            "event": "on_chat_model_start",
            "run_id": "run-1",
            "metadata": {"ls_model_name": "test-model"},
        },
        {
            "event": "on_chat_model_end",
            "run_id": "run-1",
            "metadata": {"ls_model_name": "test-model"},
            "data": {
                "output": SimpleNamespace(
                    content="hi",
                    tool_calls=[],
                    usage_metadata={"input_tokens": 10, "output_tokens": 5},
                )
            },
        },
    ]
    monkeypatch.setattr(agent, "get_graph", lambda: _FakeGraph(events))

    prompt_before = _sample("oli_llm_tokens_total", {"kind": "prompt", "model": "test-model"})
    completion_before = _sample(
        "oli_llm_tokens_total", {"kind": "completion", "model": "test-model"}
    )
    latency_before = _sample("oli_llm_call_duration_seconds_count", {"model": "test-model"})

    store = Storage()
    cid = await store.create_conversation()
    async for _ in agent.run_turn(store, cid, "hi"):
        pass

    assert _sample("oli_llm_tokens_total", {"kind": "prompt", "model": "test-model"}) == (
        prompt_before + 10
    )
    assert _sample("oli_llm_tokens_total", {"kind": "completion", "model": "test-model"}) == (
        completion_before + 5
    )
    # One call timed → the latency histogram's count advanced by exactly one.
    assert _sample("oli_llm_call_duration_seconds_count", {"model": "test-model"}) == (
        latency_before + 1
    )


async def test_agent_error_metric_incremented(monkeypatch):
    async def boom(_inputs, **_kwargs):
        raise RuntimeError("model exploded")
        yield  # pragma: no cover

    fake = _FakeGraph([])
    fake.astream_events = boom
    monkeypatch.setattr(agent, "get_graph", lambda: fake)

    before = _sample("oli_agent_errors_total", {"type": "RuntimeError"})

    store = Storage()
    cid = await store.create_conversation()
    events = [ev async for ev in agent.run_turn(store, cid, "hi")]

    assert any(e["type"] == "error" for e in events)
    assert _sample("oli_agent_errors_total", {"type": "RuntimeError"}) == before + 1
