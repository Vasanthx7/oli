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
# Persistent per-site browser profiles (cookies from a human login). One subdir
# per named profile; used by browse() so the agent reuses an authenticated session
# without ever seeing credentials. Gitignored via data/.
PROFILES_DIR = DATA_DIR / "profiles"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
PROFILES_DIR.mkdir(exist_ok=True)


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

    # --- Observability: LangSmith tracing (opt-in) for the LangGraph agent ---
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "oli"

    # --- LLM provider (Groq / any OpenAI-compatible endpoint) ---
    # `groq_*` are the base defaults. The chat/reasoning model and the browser
    # (browser-use) model each have their own endpoint/key/model so they can point
    # at different providers — e.g. a local Ollama for chat, Groq for browsing.
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"

    # Chat / reasoning model (LangGraph agent + memory extraction). Empty fields
    # inherit from groq_* below, so a pure-Groq setup needs no extra config.
    chat_base_url: str = ""
    chat_api_key: str = ""
    chat_model: str = ""

    # Browser (browser-use) model. Kept on Groq by default even when chat is local,
    # because driving a browser needs a strong model.
    browser_base_url: str = ""
    browser_api_key: str = ""
    browser_model: str = ""

    # Groq free tier allows ~30 requests/min; browse (browser-use) makes one call
    # per step, so it is paced to this ceiling to avoid 429s (0 = unlimited).
    browser_max_rpm: int = 27

    # Speech-to-text (Groq Whisper). turbo is fast + cheap; large-v3 is most accurate.
    stt_model: str = "whisper-large-v3-turbo"

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Storage ---
    # SQLite for local dev/test/CI; set to a postgresql+asyncpg:// URL in production.
    database_url: str = ""

    def model_post_init(self, __context: object) -> None:
        # Chat model inherits from groq_* unless explicitly overridden (e.g. local).
        self.chat_base_url = self.chat_base_url or self.groq_base_url
        self.chat_api_key = self.chat_api_key or self.groq_api_key
        self.chat_model = self.chat_model or self.groq_model
        # Browser model likewise inherits from groq_* (stays on Groq by default).
        self.browser_base_url = self.browser_base_url or self.groq_base_url
        self.browser_api_key = self.browser_api_key or self.groq_api_key
        self.browser_model = self.browser_model or self.groq_model
        if not self.database_url:
            self.database_url = f"sqlite+aiosqlite:///{(DATA_DIR / 'oli.db').as_posix()}"

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")

    @property
    def tracing_enabled(self) -> bool:
        """LangSmith tracing is on when a key is present (or explicitly flagged).

        Setting LANGSMITH_API_KEY alone turns tracing on — no separate flag needed.
        With no key it stays off, so tests/CI remain fully offline. To disable while
        a key is present, leave the key unset."""
        return self.langsmith_tracing or bool(self.langsmith_api_key)

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
CHAT_BASE_URL = settings.chat_base_url
CHAT_API_KEY = settings.chat_api_key
CHAT_MODEL = settings.chat_model
BROWSER_BASE_URL = settings.browser_base_url
BROWSER_API_KEY = settings.browser_api_key
BROWSER_MODEL = settings.browser_model
BROWSER_MAX_RPM = settings.browser_max_rpm
STT_MODEL = settings.stt_model
HOST = settings.host
PORT = settings.port
DATABASE_URL = settings.database_url


def require_api_key() -> str:
    return settings.require_api_key()
