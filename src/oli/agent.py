"""Turn orchestration on top of the LangGraph agent (see agent_graph.py).

`run_turn` invokes the compiled graph with `astream_events` and translates
LangGraph's event stream into the same typed events the web layer already renders,
so the API and UI are unchanged across the migration:

  {"type": "token",     "text": str}
  {"type": "tool_start","name": str, "args": dict}
  {"type": "tool_end",  "name": str, "result": str}
  {"type": "done",      "content": str}
  {"type": "error",     "message": str}

It also keeps our own responsibilities that live outside the graph: persisting
messages for the UI/history, and extracting durable memories after the turn.
"""

import asyncio
import time
from collections.abc import AsyncGenerator

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from . import memory, metrics
from .agent_graph import RECURSION_LIMIT, get_graph
from .llm import LLMClient
from .logging_config import get_logger
from .storage import Storage

log = get_logger(__name__)

# Tool results echoed to the UI are trimmed; the model still sees the full text.
_TOOL_PREVIEW_LEN = 500


def _current_request_id() -> str | None:
    """The request id bound by the HTTP middleware, if this turn runs in a request.

    Threaded into the LangGraph run's trace metadata so a LangSmith trace can be
    correlated back to the structured logs for the same turn. Scheduler-driven
    turns run outside a request and simply have none."""
    return structlog.contextvars.get_contextvars().get("request_id")


def _record_llm_metrics(event: dict, starts: dict[str, float]) -> None:
    """Record per-LLM-call latency + token usage from an on_chat_model_end event.

    Defensive on every field: the fake graph in tests emits minimal events with no
    run_id/metadata/usage, and real usage is absent unless stream_usage is on."""
    model = (event.get("metadata") or {}).get("ls_model_name") or "unknown"
    run_id = event.get("run_id")
    start = starts.pop(run_id, None) if run_id else None
    if start is not None:
        metrics.LLM_CALL_LATENCY.labels(model=model).observe(time.perf_counter() - start)
    output = event.get("data", {}).get("output")
    usage = getattr(output, "usage_metadata", None) or {}
    if usage.get("input_tokens"):
        metrics.LLM_TOKENS.labels(kind="prompt", model=model).inc(usage["input_tokens"])
    if usage.get("output_tokens"):
        metrics.LLM_TOKENS.labels(kind="completion", model=model).inc(usage["output_tokens"])


# Keep references to fire-and-forget extraction tasks so they aren't GC'd mid-run.
_background_tasks: set = set()


def _spawn_extraction(conversation_id: str, user_message: str, answer: str) -> None:
    """Extract durable facts from this exchange in the background (non-blocking)."""
    mem = memory.active()
    if mem is None or not answer:
        return
    llm = LLMClient()
    task = asyncio.create_task(mem.extract_facts(llm, user_message, answer))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _history_to_lc(history: list[dict]) -> list:
    """Convert stored rows into LangChain messages (user/assistant prose only).

    Stored 'tool' rows are UI activity records, not valid standalone chat messages
    (a tool message must follow an assistant tool_call), so they are omitted when
    seeding the graph.
    """
    out: list = []
    for m in history:
        if not m["content"]:
            continue
        if m["role"] == "user":
            out.append(HumanMessage(content=m["content"]))
        elif m["role"] == "assistant":
            out.append(AIMessage(content=m["content"]))
    return out


def _text(content) -> str:
    """Coerce message content (str, or list of content blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return str(content)


async def run_turn(
    store: Storage,
    conversation_id: str,
    user_message: str,
) -> AsyncGenerator[dict, None]:
    """Run one full user turn, yielding events. Persists user + assistant + tool messages."""
    metrics.CHAT_TURNS.inc()
    await store.add_message(conversation_id, "user", user_message)
    seed = _history_to_lc(await store.get_messages(conversation_id))

    graph = get_graph()
    final_content = ""
    # perf_counter start per LLM run_id, to time each chat-model call.
    llm_starts: dict[str, float] = {}

    # Correlate the LangSmith trace with this turn's logs via the request id.
    config: dict = {"recursion_limit": RECURSION_LIMIT}
    request_id = _current_request_id()
    if request_id:
        config["metadata"] = {"request_id": request_id}

    try:
        async for event in graph.astream_events(
            {"messages": seed},
            version="v2",
            config=config,
        ):
            kind = event["event"]
            # Which graph node produced this event. The classify node makes its own
            # LLM call; treat a missing node as the agent (keeps the fake-graph tests
            # working) so only the classifier's calls are filtered out below.
            node = (event.get("metadata") or {}).get("langgraph_node")
            from_agent = node in (None, "agent")

            if kind == "on_chat_model_start":
                run_id = event.get("run_id")
                if run_id:
                    llm_starts[run_id] = time.perf_counter()

            elif kind == "on_chat_model_stream":
                # Only the agent's tokens go to the UI — never the classifier's.
                if from_agent:
                    text = _text(event["data"]["chunk"].content)
                    if text:
                        yield {"type": "token", "text": text}

            elif kind == "on_chat_model_end":
                # Token/latency metrics count every model call (classify + agent).
                _record_llm_metrics(event, llm_starts)
                msg = event["data"]["output"]
                # The agent message with no tool calls is the final answer.
                if from_agent and not getattr(msg, "tool_calls", None):
                    final_content = _text(msg.content)

            elif kind == "on_chain_end" and event.get("name") == "classify":
                intent = (event["data"].get("output") or {}).get("intent")
                if intent:
                    metrics.INTENTS.labels(
                        category=intent["category"], complexity=intent["complexity"]
                    ).inc()
                    log.info("intent_classified", **intent)
                    yield {"type": "intent", "intent": intent}

            elif kind == "on_tool_start":
                metrics.TOOL_CALLS.labels(tool=event["name"]).inc()
                yield {
                    "type": "tool_start",
                    "name": event["name"],
                    "args": event["data"].get("input", {}),
                }

            elif kind == "on_tool_end":
                output = event["data"].get("output")
                result = _text(getattr(output, "content", output))
                await store.add_message(conversation_id, "tool", result, tool_name=event["name"])
                yield {
                    "type": "tool_end",
                    "name": event["name"],
                    "result": result[:_TOOL_PREVIEW_LEN],
                }

    except GraphRecursionError:
        metrics.AGENT_ERRORS.labels(type="GraphRecursionError").inc()
        final_content = final_content or (
            "I reached the tool-use limit before finishing. Here's what I have so far."
        )
    except Exception as e:  # noqa: BLE001 — surface any failure to the UI cleanly
        metrics.AGENT_ERRORS.labels(type=type(e).__name__).inc()
        yield {"type": "error", "message": f"{type(e).__name__}: {e}"}
        return

    await store.add_message(conversation_id, "assistant", final_content)
    _spawn_extraction(conversation_id, user_message, final_content)
    yield {"type": "done", "content": final_content}


async def run_once(store: Storage, conversation_id: str, user_message: str) -> str:
    """Run a turn autonomously (no streaming consumer) and return the final answer.

    Used by the scheduler for proactive tasks. Reuses the full graph, so tools and
    memory work in scheduled runs exactly as in interactive chat. Raises on error
    so the caller can record a failed notification.
    """
    final = ""
    async for event in run_turn(store, conversation_id, user_message):
        if event["type"] == "done":
            final = event["content"]
        elif event["type"] == "error":
            raise RuntimeError(event["message"])
    return final
