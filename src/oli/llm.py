"""Minimal Groq client (OpenAI-compatible) for non-streaming internal completions.

Since the migration to LangGraph (ADR 0003), the interactive agent uses a LangChain
chat model. This lightweight client remains for internal, non-streamed tasks — chiefly
memory fact-extraction — where we just need a single text completion.
"""

import json

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
