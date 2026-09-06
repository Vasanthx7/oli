"""The core agent loop — the heart of the product.

For each user turn:
  1. Assemble context: personality system prompt + prior history from SQLite + new message.
  2. Ask the model (streaming). Stream prose tokens out to the caller as they arrive.
  3. If the model requests tools, execute them, feed results back, and loop.
  4. When the model answers with no tool calls, the turn is done. Persist everything.

The loop yields typed events so the web layer can render streaming text and tool activity:
  {"type": "token",     "text": str}
  {"type": "tool_start","name": str, "args": dict}
  {"type": "tool_end",  "name": str, "result": str}
  {"type": "done",      "content": str}
  {"type": "error",     "message": str}
"""

import asyncio
from collections.abc import AsyncGenerator

from . import memory, tools
from .llm import LLMClient, parse_arguments
from .personality import system_prompt
from .storage import Storage

MAX_TOOL_ITERATIONS = 8
# Tool results echoed to the UI are trimmed; the model still sees the full text.
_TOOL_PREVIEW_LEN = 500

# Keep references to fire-and-forget extraction tasks so they aren't GC'd mid-run.
_background_tasks: set = set()


def _recall_block(user_message: str) -> str | None:
    """Build a system context block from memories relevant to the user's message."""
    mem = memory.active()
    if mem is None:
        return None
    try:
        hits = mem.recall(user_message)
    except Exception:
        return None
    if not hits:
        return None
    facts = "\n".join(f"- {h['content']}" for h in hits)
    return (
        "Here are things you remember about the user that may be relevant. Use them "
        "naturally when helpful; don't recite them verbatim or mention that you're "
        f"recalling memory:\n{facts}"
    )


def _spawn_extraction(llm: LLMClient, conversation_id: str, user_message: str, answer: str):
    """Extract durable facts from this exchange in the background (non-blocking)."""
    mem = memory.active()
    if mem is None or not answer:
        return
    task = asyncio.create_task(mem.extract_facts(llm, user_message, answer))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _history_to_messages(history: list[dict]) -> list[dict]:
    """Convert stored rows into OpenAI chat messages (user/assistant prose only).

    Stored 'tool' rows are activity records for replay in the UI, not valid
    standalone chat messages (a tool message must follow an assistant tool_call),
    so they are omitted when rebuilding context for the model.
    """
    out = []
    for m in history:
        if m["role"] in ("user", "assistant") and m["content"]:
            out.append({"role": m["role"], "content": m["content"]})
    return out


async def run_turn(
    store: Storage,
    conversation_id: str,
    user_message: str,
) -> AsyncGenerator[dict, None]:
    """Run one full user turn, yielding events. Persists user + assistant + tool messages."""
    llm = LLMClient()

    store.add_message(conversation_id, "user", user_message)

    messages: list[dict] = [{"role": "system", "content": system_prompt()}]

    # Inject relevant long-term memories so Oli "just knows" the user.
    recall = await asyncio.to_thread(_recall_block, user_message)
    if recall:
        messages.append({"role": "system", "content": recall})

    messages.extend(_history_to_messages(store.get_messages(conversation_id)))

    final_content = ""

    try:
        for _ in range(MAX_TOOL_ITERATIONS):
            assistant_message: dict | None = None

            # Stream one model turn.
            async for kind, payload in llm.stream_completion(messages, tools.SCHEMAS):
                if kind == "token":
                    yield {"type": "token", "text": payload}
                elif kind == "message":
                    assistant_message = payload  # type: ignore[assignment]

            if assistant_message is None:
                yield {"type": "error", "message": "No response from model."}
                return

            messages.append(assistant_message)
            tool_calls = assistant_message.get("tool_calls") or []

            if not tool_calls:
                # Plain answer — the turn is complete.
                final_content = assistant_message.get("content", "") or ""
                break

            # Execute each requested tool, then loop back to the model with results.
            for call in tool_calls:
                name = call["function"]["name"]
                args = parse_arguments(call["function"]["arguments"])

                yield {"type": "tool_start", "name": name, "args": args}
                result = await tools.run_tool(name, args)
                yield {
                    "type": "tool_end",
                    "name": name,
                    "result": result[:_TOOL_PREVIEW_LEN],
                }

                store.add_message(conversation_id, "tool", result, tool_name=name)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": result,
                    }
                )
        else:
            # Ran out of iterations without a plain answer.
            final_content = (
                final_content
                or "I reached the tool-use limit before finishing. Here's what I have so far."
            )

        store.add_message(conversation_id, "assistant", final_content)

        # Learn durable facts from this exchange in the background (best-effort).
        _spawn_extraction(llm, conversation_id, user_message, final_content)

        yield {"type": "done", "content": final_content}

    except Exception as e:  # noqa: BLE001 — surface any failure to the UI cleanly
        yield {"type": "error", "message": f"{type(e).__name__}: {e}"}


async def run_once(store: Storage, conversation_id: str, user_message: str) -> str:
    """Run a turn autonomously (no streaming consumer) and return the final answer.

    Used by the scheduler for proactive tasks. Reuses the full loop, so tools and
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
