"""Central, typed configuration via pydantic-settings.

Values come from environment variables (and a local .env in development).
`settings` is the single source of truth; the uppercase module-level names are
kept as thin aliases so existing imports (`config.GROQ_MODEL`) keep working while
we migrate the codebase toward `settings.groq_model`.
"""

from dataclasses import dataclass
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class CloudProvider:
    """One resolved cloud LLM provider in the fallback chain (see ADR 0017).

    ``model`` is the strong/reasoning tier; ``fast_model`` the cheap tier used by the
    intent classifier and simple chat turns. All cloud providers are OpenAI-compatible,
    so a provider is fully described by an endpoint + key + two model ids.
    """

    name: str
    base_url: str
    api_key: str
    model: str
    fast_model: str


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

    # --- Cloud LLM providers (chat / reasoning / intent / memory) — see ADR 0017 ---
    # All non-vision work runs on a *cloud* provider with automatic failover: the chain
    # is tried in order (default Groq -> Mistral), so if the primary is down/rate-limited
    # the next one answers. Local models are used ONLY for computer-use (see fara_* below).
    # Each provider is OpenAI-compatible: endpoint + key + a strong `model` + a cheap
    # `fast_model` (intent classifier + simple chat turns).
    #
    # Provider 1 — Groq.
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "openai/gpt-oss-120b"
    # Fast tier (intent classifier + simple chat turns). gpt-oss-20b is the small sibling
    # of the 120b — tool-capable and on the same account. (The old llama-3.1-8b-instant
    # default is no longer served on our Groq plan — it 404s.)
    groq_fast_model: str = "openai/gpt-oss-20b"

    # Provider 2 — Mistral (failover). Add a MISTRAL_API_KEY to enable it; with no key
    # it is dropped from the chain (no dead fallback). Endpoints/models are Mistral's.
    mistral_api_key: str = ""
    mistral_base_url: str = "https://api.mistral.ai/v1"
    mistral_model: str = "mistral-large-latest"
    mistral_fast_model: str = "mistral-small-latest"

    # Ordered failover chain, by provider name. Only providers with a key are used; the
    # first with a key is the primary. Reorder (e.g. "mistral,groq") to prefer another.
    cloud_provider_order: str = "groq,mistral"

    # --- Browse engine (computer-use) ---
    # "fara" = the native Fara-1.5 computer-use loop (vision-only, its own action loop)
    # against a local Ollama serving Fara1.5-4B — the sole engine and the only local
    # workload (see ADR 0016/0017). It has NO cloud fallback by design (see fara_browse):
    # if the model host is offline the tool returns a clear "unavailable" message rather
    # than degrading to a weaker path. Kept as a setting for future engines/tests.
    browse_engine: str = "fara"
    # Local Ollama serving the Fara model. In production the deploy VM reaches back
    # to the developer's machine, so this is typically a LAN URL, not localhost.
    fara_base_url: str = "http://localhost:11434/v1"
    fara_api_key: str = "ollama"  # Ollama ignores it; the OpenAI client requires one.
    fara_model: str = "fara15-4b"
    # "Careful" tier for interaction-heavy / dense-page tasks (add-to-cart, forms,
    # commerce): 4B grounds well on clean read/nav pages but lands near — not on —
    # small controls on cluttered pages, where 9B is reliable (benchmark + Amazon
    # debugging). When fara_autoroute is on, browse picks this for such tasks.
    fara_model_heavy: str = "fara15-9b"
    fara_autoroute: bool = True
    # When the Fara host is unreachable, optionally point users at a recorded
    # walkthrough of the workflow instead of a bare error (empty = no link).
    fara_unavailable_url: str = ""
    # Debug aid: save per-step screenshots + a steps.jsonl under data/fara_traces/.
    # Off by default (extra disk/latency); flip on to inspect grounding failures.
    fara_save_traces: bool = False

    # Speech-to-text (Groq Whisper). turbo is fast + cheap; large-v3 is most accurate.
    stt_model: str = "whisper-large-v3-turbo"

    # --- Agent harness guardrails (per-turn safety rails beyond RECURSION_LIMIT) ---
    # Max tool executions in a single turn; hitting it ends the turn gracefully.
    max_tool_calls_per_turn: int = 8
    # Per-turn ceiling on total LLM tokens (prompt+completion); 0 = disabled. Ends the
    # turn gracefully once exceeded, so a pathological loop can't burn unbounded tokens.
    per_turn_token_budget: int = 0
    # Hard timeout on a single tool call in seconds; 0 = disabled. Generous by default
    # so browse (bounded internally by MAX_STEPS) isn't cut short, while still capping
    # a truly hung tool.
    tool_timeout_seconds: int = 180

    # --- Server ---
    host: str = "127.0.0.1"
    port: int = 8000

    # --- Access control (HTTP Basic Auth over the whole app) ---
    # The app faces the public internet (ADR 0012) but is single-user (ADR 0008), so
    # every endpoint sits behind Basic Auth. Auth is ENABLED only when a password is
    # set — leaving it empty keeps local dev, tests, and CI open (and offline). Set a
    # strong AUTH_PASSWORD in any internet-facing deploy (startup warns if you don't).
    auth_username: str = "oli"
    auth_password: str = ""

    @property
    def auth_enabled(self) -> bool:
        return bool(self.auth_password)

    # --- Storage ---
    # SQLite for local dev/test/CI; set to a postgresql+asyncpg:// URL in production.
    database_url: str = ""

    def model_post_init(self, __context: object) -> None:
        if not self.database_url:
            self.database_url = f"sqlite+aiosqlite:///{(DATA_DIR / 'oli.db').as_posix()}"

    # --- Cloud provider chain ------------------------------------------------
    @property
    def _provider_registry(self) -> dict[str, CloudProvider]:
        return {
            "groq": CloudProvider(
                "groq", self.groq_base_url, self.groq_api_key, self.groq_model, self.groq_fast_model
            ),
            "mistral": CloudProvider(
                "mistral",
                self.mistral_base_url,
                self.mistral_api_key,
                self.mistral_model,
                self.mistral_fast_model,
            ),
        }

    @property
    def cloud_providers(self) -> list[CloudProvider]:
        """The ordered failover chain of *configured* cloud providers (those with a key).

        Order follows ``cloud_provider_order``; unkeyed providers are dropped so we never
        fail over to a dead endpoint. If nothing is keyed we still return Groq, so callers
        get a clear auth error rather than an empty chain."""
        registry = self._provider_registry
        chain: list[CloudProvider] = []
        for name in (n.strip().lower() for n in self.cloud_provider_order.split(",")):
            provider = registry.get(name)
            if provider and provider.api_key and provider not in chain:
                chain.append(provider)
        return chain or [registry["groq"]]

    @property
    def primary_provider(self) -> CloudProvider:
        """The first configured provider — the one tried before any failover."""
        return self.cloud_providers[0]

    # Back-compat convenience: chat_* now derive from the primary provider so existing
    # references keep working (the provider layer uses `cloud_providers` for failover).
    @property
    def chat_base_url(self) -> str:
        return self.primary_provider.base_url

    @property
    def chat_api_key(self) -> str:
        return self.primary_provider.api_key

    @property
    def chat_model(self) -> str:
        return self.primary_provider.model

    @property
    def chat_fast_model(self) -> str:
        return self.primary_provider.fast_model

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
CHAT_FAST_MODEL = settings.chat_fast_model
STT_MODEL = settings.stt_model
HOST = settings.host
PORT = settings.port
DATABASE_URL = settings.database_url


def require_api_key() -> str:
    return settings.require_api_key()
