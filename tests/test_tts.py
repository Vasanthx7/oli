"""Offline tests for the TTS synthesizer (Groq audio.speech), with the client mocked."""

from oli import tts
from oli.config import settings


def test_enabled_and_voices(monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", "")
    assert tts.enabled() is False
    assert tts.voices() == []

    monkeypatch.setattr(settings, "tts_provider", "groq")
    assert tts.enabled() is True
    assert "Celeste-PlayAI" in tts.voices()


class _Resp:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def read(self):
        return b"AUDIO"


class _StreamingResponse:
    def __init__(self, captured):
        self._captured = captured

    def create(self, **kwargs):
        self._captured.update(kwargs)
        return _Resp()


class _Speech:
    def __init__(self, captured):
        self.with_streaming_response = _StreamingResponse(captured)


class _Audio:
    def __init__(self, captured):
        self.speech = _Speech(captured)


def _fake_client_factory(captured):
    class _Client:
        def __init__(self, **_kw):
            self.audio = _Audio(captured)

    return _Client


async def test_synthesize_calls_client_and_returns_bytes(monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "k")  # require_api_key passes
    monkeypatch.setattr(settings, "tts_model", "playai-tts")
    monkeypatch.setattr(settings, "tts_voice", "Celeste-PlayAI")

    captured: dict = {}
    monkeypatch.setattr(tts, "AsyncOpenAI", _fake_client_factory(captured))

    audio = await tts.Synthesizer().synthesize("hello world", voice="Fritz-PlayAI")
    assert audio == b"AUDIO"
    assert captured["voice"] == "Fritz-PlayAI"
    assert captured["input"] == "hello world"
    assert captured["model"] == "playai-tts"


async def test_synthesize_defaults_voice_and_clips(monkeypatch):
    monkeypatch.setattr(settings, "tts_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "k")
    monkeypatch.setattr(settings, "tts_voice", "Celeste-PlayAI")
    monkeypatch.setattr(settings, "tts_max_chars", 5)

    captured: dict = {}
    monkeypatch.setattr(tts, "AsyncOpenAI", _fake_client_factory(captured))

    await tts.Synthesizer().synthesize("abcdefghij")  # no voice -> default; clipped to 5
    assert captured["voice"] == "Celeste-PlayAI"
    assert captured["input"] == "abcde"
