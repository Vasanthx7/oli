"""Optional LangSmith tracing for the LangGraph agent.

LangChain reads its tracing config from environment variables. Our settings come
from a typed .env via pydantic-settings (which does not populate os.environ), so
when tracing is enabled we export the variables LangChain expects. Off by default;
enable by setting LANGSMITH_TRACING=true and LANGSMITH_API_KEY in the environment.
"""

import os

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)


def configure_tracing() -> None:
    if settings.langsmith_tracing and settings.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
        log.info("langsmith_tracing_enabled", project=settings.langsmith_project)
