"""Central, typed configuration via pydantic-settings.

Values come from environment variables (and a local .env in development).
`settings` is the single source of truth; the uppercase module-level names are
kept as thin aliases so existing imports (`config.GROQ_MODEL`) keep working while
we migrate the codebase toward `settings.groq_model`.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (src/oli/config.py -> project root).
ROOT = Path(__file__).resolve().parents[2]

# --- Paths (not configurable) ---
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
WEB_DIR = ROOT / "web"
PERSONALITY_FILE = ROOT / "personality.md"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    """Application settings, validated and typed at startup."""

    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Runtime ---
    environment: str = "development"
    log_level: str = "INFO"
    log_json: bool = False

    # --- LLM provider (Groq / any OpenAI-compatible endpoint) ---
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"
    browser_model: str = ""  # defaults to groq_model if unset (see below)

    # Speech-to-text (Groq Whisper). turbo is fast + cheap; large-v3 is most accurate.
    stt_model: str = "whisper-large-v3-turbo"

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Storage ---
    # SQLite for local dev/test/CI; set to a postgresql+asyncpg:// URL in production.
    database_url: str = ""

    def model_post_init(self, __context: object) -> None:
        if not self.browser_model:
            self.browser_model = self.groq_model
        if not self.database_url:
            self.database_url = f"sqlite+aiosqlite:///{(DATA_DIR / 'oli.db').as_posix()}"

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    def require_api_key(self) -> str:
        """Return the LLM API key or raise a clear error if it is missing."""
        if not self.groq_api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        return self.groq_api_key


settings = Settings()

# --- Backward-compatible aliases (single source of truth = `settings`) ---
GROQ_API_KEY = settings.groq_api_key
GROQ_BASE_URL = settings.groq_base_url
GROQ_MODEL = settings.groq_model
BROWSER_MODEL = settings.browser_model
STT_MODEL = settings.stt_model
HOST = settings.host
PORT = settings.port
DATABASE_URL = settings.database_url


def require_api_key() -> str:
    return settings.require_api_key()
