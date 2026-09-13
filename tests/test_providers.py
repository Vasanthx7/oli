"""Offline tests for the cloud provider layer (Groq -> Mistral failover).

No network: ChatOpenAI construction and `.with_fallbacks()` composition are lazy, so
we can assert on the *shape* of what gets built and on the model->provider mapping used
to surface a fallback notice — all without an API call.
"""

from langchain_core.runnables import RunnableWithFallbacks

from oli import providers
from oli.config import settings


def test_single_provider_is_a_bare_model():
    # Default test env keys only Groq → no fallback wrapper, just the client.
    model = providers.build_chat_model("reasoning")
    assert not isinstance(model, RunnableWithFallbacks)


def test_two_providers_compose_with_fallbacks(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "mk")
    model = providers.build_chat_model("reasoning")
    assert isinstance(model, RunnableWithFallbacks)


def test_structured_model_also_composes(monkeypatch):
    from pydantic import BaseModel

    class Out(BaseModel):
        x: int

    monkeypatch.setattr(settings, "mistral_api_key", "mk")
    model = providers.build_structured_model(Out, tier="fast")
    assert isinstance(model, RunnableWithFallbacks)


def test_provider_of_model_maps_both_tiers(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "mk")
    assert providers.provider_of_model(settings.groq_model) == "groq"
    assert providers.provider_of_model(settings.groq_fast_model) == "groq"
    assert providers.provider_of_model(settings.mistral_model) == "mistral"
    assert providers.provider_of_model(settings.mistral_fast_model) == "mistral"
    assert providers.provider_of_model("nope") is None
    assert providers.provider_of_model(None) is None


def test_is_fallback_model(monkeypatch):
    monkeypatch.setattr(settings, "mistral_api_key", "mk")
    # Primary (groq) answering is NOT a fallback; mistral answering IS.
    assert providers.is_fallback_model(settings.groq_model) is False
    assert providers.is_fallback_model(settings.mistral_model) is True
    assert providers.is_fallback_model(None) is False


def test_no_fallback_flagged_when_only_one_provider():
    # Only Groq keyed → nothing is ever "a fallback".
    assert providers.is_fallback_model(settings.groq_model) is False
    assert providers.is_fallback_model(settings.mistral_model) is False
