"""Groq chat client (OpenAI-compatible), with streaming that also captures tool calls.

The OpenAI streaming protocol delivers text and tool-call arguments as incremental
deltas. `stream_completion` yields ("token", text) events as prose arrives and, when
the turn finishes, a final ("message", assistant_message) event carrying the fully
assembled message (content + any tool_calls) so the agent loop can act on it.
"""

import json
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI

from . import config


class LLMClient:
    def __init__(self):
        config.require_api_key()
        self._client = AsyncOpenAI(
            api_key=config.GROQ_API_KEY,
            base_url=config.GROQ_BASE_URL,
        )
        self.model = config.GROQ_MODEL

    async def stream_completion(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
    ) -> AsyncGenerator[tuple[str, object], None]:
        """Stream one model turn.

        Yields:
            ("token", str)     — a chunk of assistant prose as it arrives
            ("message", dict)  — the final assembled assistant message, emitted once at the end
        """
        kwargs: dict = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = await self._client.chat.completions.create(**kwargs)

        content_parts: list[str] = []
        # tool_calls accumulated by index -> {id, name, arguments(str)}
        tool_calls: dict[int, dict] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta

            if delta.content:
                content_parts.append(delta.content)
                yield ("token", delta.content)

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    slot = tool_calls.setdefault(
                        tc.index, {"id": None, "name": "", "arguments": ""}
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["arguments"] += tc.function.arguments

        assistant_message: dict = {
            "role": "assistant",
            "content": "".join(content_parts),
        }
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for _, tc in sorted(tool_calls.items())
            ]

        yield ("message", assistant_message)

    async def complete(self, messages: list[dict], temperature: float = 0.0) -> str:
        """Non-streaming completion; returns the assistant's text. Used for internal
        tasks like memory fact-extraction where we don't need to stream to the user."""
        resp = await self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            stream=False,
        )
        return resp.choices[0].message.content or ""


def parse_arguments(raw: str) -> dict:
    """Best-effort parse of a tool call's JSON arguments string."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
