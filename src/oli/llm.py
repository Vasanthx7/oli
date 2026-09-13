"""Minimal OpenAI-compatible client for non-streaming internal completions.

Since the migration to LangGraph (ADR 0003), the interactive agent uses a LangChain
chat model. This lightweight client remains for internal, non-streamed tasks — chiefly
memory fact-extraction — where we just need a single text completion. It runs over the
same cloud provider chain as the agent (Groq -> Mistral failover): each provider is
tried in order until one answers — see :mod:`oli.providers` and ADR 0017.
"""

import json

from openai import AsyncOpenAI, AsyncStream

from . import config
from .logging_config import get_logger

log = get_logger(__name__)


class LLMClient:
    async def complete(self, messages: list[dict], temperature: float = 0.0) -> str:
        """Non-streaming completion over the cloud chain; returns the assistant's text.

        Tries each configured provider in order, failing over on any error, and raises
        the last error only if every provider fails (callers treat that as best-effort).
        """
        last_error: Exception | None = None
        for provider in config.settings.cloud_providers:
            client = AsyncOpenAI(api_key=provider.api_key or "local", base_url=provider.base_url)
            try:
                resp = await client.chat.completions.create(
                    model=provider.model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    stream=False,
                )
                assert not isinstance(resp, AsyncStream)  # stream=False → a ChatCompletion
                return resp.choices[0].message.content or ""
            except Exception as e:  # noqa: BLE001 — try the next provider in the chain
                last_error = e
                log.warning("llm_provider_failed", provider=provider.name, error=str(e))
        if last_error is not None:
            raise last_error
        return ""


def parse_arguments(raw: str) -> dict:
    """Best-effort parse of a tool call's JSON arguments string."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}
