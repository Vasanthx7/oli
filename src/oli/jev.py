"""Jev (TypeSafe System-One) decision model — typed, fast decisions, no text.

Jev returns a *predefined typed value* plus a calibrated confidence, not prose. We use it
ONLY at structured decision points (intent classification first), never for text generation
(it cannot emit ``str``). It integrates via Pydantic AI's TypeSafe provider.

Both the dependency and the early-access API are treated as optional: the module imports
even when ``pydantic-ai`` isn't installed (imports are lazy), and :func:`decide` raises on
any missing key / missing dep / timeout / API error. **Every caller must fail over to the
existing LLM path** — Jev must never break a turn (see :func:`oli.intent.classify`).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, NamedTuple

from pydantic import BaseModel

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)

# Pydantic-AI Agents are cheap but not free to build; cache one per (schema, model).
_agents: dict[tuple[type, str], Any] = {}


class JevResult(NamedTuple):
    """One typed decision: the filled schema, per-field confidence, and input-token count."""

    output: BaseModel
    confidence: dict[str, float]
    input_tokens: int


def available() -> bool:
    """True when Jev is configured (has an API key). Dependency import is checked at call time."""
    return bool(settings.jev_api_key)


def _agent(schema: type[BaseModel]) -> Any:
    """Lazily build + cache a Pydantic-AI Agent bound to this output schema + the Jev model."""
    key = (schema, settings.jev_model)
    agent = _agents.get(key)
    if agent is None:
        from pydantic_ai import Agent
        from pydantic_ai.models.typesafe import TypeSafeModel
        from pydantic_ai.providers.typesafe import TypeSafeProvider

        model = TypeSafeModel(
            settings.jev_model,
            provider=TypeSafeProvider(api_key=settings.jev_api_key),
        )
        agent = Agent(model, output_type=schema)
        _agents[key] = agent
    return agent


async def decide(schema: type[BaseModel], prompt: str) -> JevResult:
    """Return a typed decision + per-field confidence + input tokens for ``prompt``.

    Raises on missing key, missing dependency, timeout, or API error — the caller MUST fail
    over to the LLM path (Jev is early-access; never let it break a turn)."""
    if not settings.jev_api_key:
        raise RuntimeError("jev not configured (no jev_api_key)")
    agent = _agent(schema)
    result = await asyncio.wait_for(agent.run(prompt), timeout=settings.jev_timeout_seconds)

    confidence: dict[str, float] = {}
    with contextlib.suppress(Exception):  # provider_details shape is early-access; be defensive
        confidence = dict((result.response.provider_details or {}).get("confidence") or {})
    input_tokens = 0
    with contextlib.suppress(Exception):
        input_tokens = int(result.usage().request_tokens or 0)

    return JevResult(output=result.output, confidence=confidence, input_tokens=input_tokens)
