"""Cloud LLM provider layer with automatic failover (see ADR 0017).

Every non-vision workload — the LangGraph agent (reasoning + fast tiers), the intent
classifier, and memory fact-extraction — runs through an ordered chain of cloud
providers (default Groq -> Mistral, from ``settings.cloud_providers``). Each provider
is OpenAI-compatible, so we build one LangChain ``ChatOpenAI`` per provider and compose
them with ``.with_fallbacks()``: if the primary raises (endpoint down, rate-limited, bad
auth) the next one answers. Local models are **never** in this chain — they serve
computer-use only (see ``tools/fara_browse.py``).

The composition is transparent to callers: they get a single ``Runnable`` that behaves
like one chat model but is resilient. To let the UI tell the user *which* provider
answered (so a silent failover isn't actually silent), :func:`is_fallback_model` maps a
response's model id back to its provider and reports whether that was a non-primary one.
"""

from typing import Any

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from .config import CloudProvider, settings


def _client(provider: CloudProvider, model: str, **kwargs: Any) -> ChatOpenAI:
    """One OpenAI-compatible chat client for ``provider`` using model id ``model``."""
    return ChatOpenAI(
        model=model,
        api_key=SecretStr(provider.api_key or "local"),
        base_url=provider.base_url,
        **kwargs,
    )


def _tier_model(provider: CloudProvider, tier: str) -> str:
    """The provider's model id for ``tier`` ("fast" = cheap tier, else strong tier)."""
    return provider.fast_model if tier == "fast" else provider.model


def build_chat_model(
    tier: str = "reasoning", *, tools: list | None = None, **kwargs: Any
) -> Runnable:
    """A chat model over the cloud chain: the primary with the rest as ordered fallbacks.

    ``tier`` selects the strong (``"reasoning"``) or cheap (``"fast"``) model id per
    provider. ``tools`` are bound to *each* provider's client before composing, so the
    whole chain is tool-capable. Extra kwargs (temperature, stream_usage, ...) pass
    through to every client. With a single configured provider this is just that client.
    """

    def one(provider: CloudProvider) -> Runnable:
        client = _client(provider, _tier_model(provider, tier), **kwargs)
        return client.bind_tools(tools) if tools else client

    providers = settings.cloud_providers
    primary = one(providers[0])
    fallbacks = [one(p) for p in providers[1:]]
    return primary.with_fallbacks(fallbacks) if fallbacks else primary


def build_structured_model(schema: Any, tier: str = "fast", **kwargs: Any) -> Runnable:
    """Cloud chain constrained to emit ``schema`` (used by the intent classifier)."""

    def one(provider: CloudProvider) -> Runnable:
        client = _client(provider, _tier_model(provider, tier), **kwargs)
        return client.with_structured_output(schema)

    providers = settings.cloud_providers
    primary = one(providers[0])
    fallbacks = [one(p) for p in providers[1:]]
    return primary.with_fallbacks(fallbacks) if fallbacks else primary


def provider_of_model(model_id: str | None) -> str | None:
    """Map a response's model id back to the configured provider that serves it."""
    if not model_id:
        return None
    for provider in settings.cloud_providers:
        if model_id in (provider.model, provider.fast_model):
            return provider.name
    return None


def is_fallback_model(model_id: str | None) -> bool:
    """True when ``model_id`` came from a configured provider other than the primary.

    Used to surface a "primary was down, answered via <fallback>" notice to the user."""
    name = provider_of_model(model_id)
    return name is not None and name != settings.primary_provider.name
