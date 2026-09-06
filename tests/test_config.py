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
