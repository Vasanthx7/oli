"""Text-to-speech via Groq PlayAI (OpenAI-compatible audio.speech).

Reuses the same Groq key/endpoint as the chat brain and STT — no second provider. OFF by
default (``tts_provider=""``): the frontend falls back to the browser voice. When enabled,
the frontend posts reply text to ``/api/tts`` and plays the returned audio, still falling
back to the browser voice on any failure — TTS must never block or break a reply.
"""

from openai import AsyncOpenAI

from . import config

# Groq PlayAI English voices (see console.groq.com/docs/text-to-speech). Exposed via
# /api/tts/voices so the frontend picker can list the real server voices.
GROQ_PLAYAI_VOICES = [
    "Arista-PlayAI",
    "Atlas-PlayAI",
    "Basil-PlayAI",
    "Briggs-PlayAI",
    "Calum-PlayAI",
    "Celeste-PlayAI",
    "Cheyenne-PlayAI",
    "Chip-PlayAI",
    "Cillian-PlayAI",
    "Deedee-PlayAI",
    "Fritz-PlayAI",
    "Gail-PlayAI",
    "Indigo-PlayAI",
    "Mamaw-PlayAI",
    "Mason-PlayAI",
    "Mikail-PlayAI",
    "Mitch-PlayAI",
    "Quinn-PlayAI",
    "Thunder-PlayAI",
]


def enabled() -> bool:
    """True when server-side TTS is turned on (a provider is configured)."""
    return bool(config.settings.tts_provider)


def voices() -> list[str]:
    """Available voice names for the configured provider (for the frontend picker)."""
    return GROQ_PLAYAI_VOICES if config.settings.tts_provider == "groq" else []


class Synthesizer:
    def __init__(self) -> None:
        config.require_api_key()
        self._client = AsyncOpenAI(
            api_key=config.GROQ_API_KEY,
            base_url=config.GROQ_BASE_URL,
        )
        self.model = config.settings.tts_model
        self.default_voice = config.settings.tts_voice
        self.fmt = config.settings.tts_format

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Return audio bytes for ``text`` in the configured format. Raises on failure."""
        clipped = text[: config.settings.tts_max_chars]
        async with self._client.audio.speech.with_streaming_response.create(
            model=self.model,
            voice=voice or self.default_voice,
            input=clipped,
            response_format=self.fmt,  # type: ignore[arg-type]
        ) as resp:
            return await resp.read()
