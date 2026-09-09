"""Minimal OpenAI-compatible client for non-streaming internal completions.

Since the migration to LangGraph (ADR 0003), the interactive agent uses a LangChain
chat model. This lightweight client remains for internal, non-streamed tasks — chiefly
memory fact-extraction — where we just need a single text completion. It uses the
`chat_*` endpoint, so it follows the reasoning model (local or Groq) — see ADR 0010.
"""

import json

from openai import AsyncOpenAI

from . import config


class LLMClient:
    def __init__(self):
        # No hard key requirement: local endpoints (Ollama) accept any token.
        self._client = AsyncOpenAI(
            api_key=config.settings.chat_api_key or "local",
            base_url=config.settings.chat_base_url,
        )
        self.model = config.settings.chat_model

    async def complete(self, messages: list[dict], temperature: float = 0.0) -> str:
        """Non-streaming completion; returns the assistant's text."""
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
