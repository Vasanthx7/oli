from oli.config import Settings, settings


def test_settings_loaded():
    assert settings.groq_model
    # browser_model defaults to groq_model when unset
    assert settings.browser_model == settings.groq_model


def test_require_api_key_present():
    assert settings.require_api_key() == "test-key-not-real"


def test_require_api_key_missing_raises():
    s = Settings(groq_api_key="", _env_file=None)
    try:
        s.require_api_key()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "GROQ_API_KEY" in str(e)


def test_chat_and_browser_inherit_groq_by_default():
    s = Settings(
        groq_api_key="gk",
        groq_base_url="https://groq",
        groq_model="big-model",
        _env_file=None,
    )
    # Unset chat_*/browser_* fall back to the groq_* base.
    assert s.chat_base_url == "https://groq" and s.chat_model == "big-model"
    assert s.browser_base_url == "https://groq" and s.browser_model == "big-model"
    assert s.chat_api_key == "gk" and s.browser_api_key == "gk"


def test_hybrid_overrides_chat_only():
    s = Settings(
        groq_api_key="gk",
        groq_base_url="https://groq",
        groq_model="big-model",
        chat_base_url="http://localhost:11434/v1",
        chat_model="qwen2.5:7b-instruct",
        chat_api_key="ollama",
        _env_file=None,
    )
    # Chat points local; browser stays on Groq.
    assert s.chat_base_url == "http://localhost:11434/v1"
    assert s.chat_model == "qwen2.5:7b-instruct"
    assert s.browser_base_url == "https://groq"
    assert s.browser_model == "big-model"
    assert s.browser_api_key == "gk"
