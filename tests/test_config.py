from oli.config import Settings, settings


def test_settings_loaded():
    assert settings.groq_model
    # The primary provider drives the back-compat chat_* aliases.
    assert settings.chat_model == settings.primary_provider.model


def test_require_api_key_present():
    assert settings.require_api_key() == "test-key-not-real"


def test_require_api_key_missing_raises():
    s = Settings(groq_api_key="", _env_file=None)
    try:
        s.require_api_key()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "GROQ_API_KEY" in str(e)


def test_groq_only_chain_when_no_mistral_key():
    # With only Groq keyed, the chain is Groq alone (no dead fallback) and it is primary.
    s = Settings(
        groq_api_key="gk",
        groq_base_url="https://groq",
        groq_model="big-model",
        groq_fast_model="small-model",
        _env_file=None,
    )
    assert [p.name for p in s.cloud_providers] == ["groq"]
    assert s.primary_provider.name == "groq"
    # chat_* aliases derive from the primary provider.
    assert s.chat_base_url == "https://groq"
    assert s.chat_model == "big-model"
    assert s.chat_fast_model == "small-model"
    assert s.chat_api_key == "gk"


def test_mistral_joins_chain_when_keyed():
    s = Settings(groq_api_key="gk", mistral_api_key="mk", _env_file=None)
    names = [p.name for p in s.cloud_providers]
    assert names == ["groq", "mistral"]  # default order, both keyed
    assert s.primary_provider.name == "groq"


def test_unkeyed_provider_dropped_from_chain():
    # Mistral has no key → dropped even though it is in the order.
    s = Settings(
        groq_api_key="gk",
        mistral_api_key="",
        cloud_provider_order="mistral,groq",
        _env_file=None,
    )
    assert [p.name for p in s.cloud_providers] == ["groq"]


def test_provider_order_selects_primary():
    s = Settings(
        groq_api_key="gk",
        mistral_api_key="mk",
        cloud_provider_order="mistral,groq",
        _env_file=None,
    )
    assert [p.name for p in s.cloud_providers] == ["mistral", "groq"]
    assert s.primary_provider.name == "mistral"


def test_chain_falls_back_to_groq_when_nothing_keyed():
    # No keys at all → we still return Groq so callers get a clear auth error.
    s = Settings(groq_api_key="", mistral_api_key="", _env_file=None)
    assert [p.name for p in s.cloud_providers] == ["groq"]


def test_browse_engine_defaults_to_fara():
    s = Settings(groq_api_key="gk", _env_file=None)
    assert s.browse_engine == "fara"


def test_tracing_off_without_key():
    # No LangSmith key and no explicit flag → tracing stays off (offline default).
    s = Settings(langsmith_api_key="", langsmith_tracing=False, _env_file=None)
    assert s.tracing_enabled is False


def test_tracing_on_when_key_present():
    # A key alone turns tracing on — no separate flag needed.
    s = Settings(langsmith_api_key="ls-key", langsmith_tracing=False, _env_file=None)
    assert s.tracing_enabled is True


def test_tracing_on_when_explicitly_flagged():
    s = Settings(langsmith_api_key="", langsmith_tracing=True, _env_file=None)
    assert s.tracing_enabled is True
