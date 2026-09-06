"""Speech-to-text via Groq Whisper (OpenAI-compatible audio.transcriptions).

Reuses the same Groq key/endpoint as the chat brain — no second provider. The
browser records mic audio (webm/opus) and posts the bytes; Whisper returns a
transcript that flows into the normal agent loop, so tools and memory work in
voice exactly as they do in text.
"""

from openai import AsyncOpenAI

from . import config


class Transcriber:
    def __init__(self):
        config.require_api_key()
        self._client = AsyncOpenAI(
            api_key=config.GROQ_API_KEY,
            base_url=config.GROQ_BASE_URL,
        )
        self.model = config.STT_MODEL

    async def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm") -> str:
        """Return the transcript text for a chunk of recorded audio."""
        resp = await self._client.audio.transcriptions.create(
            model=self.model,
            file=(filename, audio_bytes),
        )
        return (resp.text or "").strip()
