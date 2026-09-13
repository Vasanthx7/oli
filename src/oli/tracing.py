"""Optional LangSmith tracing for the LangGraph agent.

LangChain reads its tracing config from environment variables. Our settings come
from a typed .env via pydantic-settings (which does not populate os.environ), so
when tracing is enabled we export the variables LangChain expects.

Tracing turns on automatically whenever a LangSmith API key is present (see
`Settings.tracing_enabled`) — no separate flag to remember. With no key it stays
off, so CI/tests remain fully offline.
"""

import os

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)


def configure_tracing() -> None:
    if not settings.tracing_enabled:
        return
    if not settings.langsmith_api_key:
        # Flag set but no key — can't trace; say so rather than fail silently.
        log.warning("langsmith_tracing_requested_without_key")
        return
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    log.info("langsmith_tracing_enabled", project=settings.langsmith_project)
