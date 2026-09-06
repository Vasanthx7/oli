"""Central configuration, loaded once from the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = two levels up from this file (src/oli/config.py -> project root).
ROOT = Path(__file__).resolve().parents[2]

load_dotenv(ROOT / ".env")

# --- LLM ---
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
BROWSER_MODEL = os.environ.get("BROWSER_MODEL", GROQ_MODEL)

# Speech-to-text (Groq Whisper). turbo is fast + cheap; large-v3 is most accurate.
STT_MODEL = os.environ.get("STT_MODEL", "whisper-large-v3-turbo")

# --- Server ---
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))

# --- Paths ---
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
WEB_DIR = ROOT / "web"
PERSONALITY_FILE = ROOT / "personality.md"
DB_PATH = DATA_DIR / "oli.db"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


def require_api_key() -> str:
    """Return the Groq API key or raise a clear error if it is missing."""
    if not GROQ_API_KEY:
        raise RuntimeError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return GROQ_API_KEY
