"""Intent classification: a cheap-model pre-pass that tags each turn.

Runs before the agent node in the graph (START -> classify -> agent). Produces a
structured `Intent` stored in graph state, giving downstream stages a signal to
key on: model routing (Phase 3 picks a tier from `complexity`/`needs_tools`) and
observability (the intent is emitted to metrics, traces, and logs).

Classification is best-effort: any failure (bad JSON, endpoint down, no key) falls
back to a safe default so the turn still runs — the agent keeps all its tools
regardless, so a wrong or missing intent only affects routing, never capability.
"""

from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr

from .config import settings
from .logging_config import get_logger

log = get_logger(__name__)

Category = Literal["chat", "tools", "browse", "memory"]
Complexity = Literal["simple", "hard"]


class Intent(BaseModel):
    """A structured read of what the user's latest message wants."""

    category: Category = Field(
        description=(
            "chat = conversational / answerable from general knowledge; "
            "tools = needs web search or page fetch; "
            "browse = needs autonomous multi-step web browsing (navigation, logged-in "
            "actions); memory = explicitly saving or recalling personal facts."
        )
    )
    complexity: Complexity = Field(
        description="simple = one direct step; hard = multi-step reasoning or several tools."
    )
    needs_tools: bool = Field(description="Whether answering likely requires any tool call.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in this classification 0-1.")
    normalized_query: str = Field(
        description="The user's request restated as a clear, standalone query."
    )


_CLASSIFIER_SYSTEM = (
    "You classify the intent of a single user message to a personal AI assistant "
    "that can chat, search the web, fetch pages, browse autonomously, and store/recall "
    "personal memories. Return only the structured classification — do not answer the "
    "message. Prefer 'chat' with needs_tools=false when general knowledge suffices; "
    "choose 'tools'/'browse' only when fresh or external information is actually needed."
)


def _fallback(query: str) -> Intent:
    """Safe default when classification fails: treat as simple chat, low confidence."""
    return Intent(
        category="chat",
        complexity="simple",
        needs_tools=False,
        confidence=0.0,
        normalized_query=query,
    )


_classifier = None


def get_classifier():
    """Lazy singleton: a fast-tier model constrained to emit an `Intent`.

    Uses the chat endpoint (the fast tier once Phase 3 splits tiers). Constructed
    lazily so importing this module needs no API key (matches the graph's pattern)."""
    global _classifier
    if _classifier is None:
        model = ChatOpenAI(
            model=settings.chat_model,
            api_key=SecretStr(settings.chat_api_key or "local"),
            base_url=settings.chat_base_url,
            temperature=0,
        )
        _classifier = model.with_structured_output(Intent)
    return _classifier


async def classify(query: str) -> Intent:
    """Classify a single user query into an `Intent`. Never raises — degrades to chat."""
    try:
        result = await get_classifier().ainvoke(
            [SystemMessage(content=_CLASSIFIER_SYSTEM), HumanMessage(content=query)]
        )
    except Exception as e:  # noqa: BLE001 — classification must never break a turn
        log.warning("intent_classification_failed", error=f"{type(e).__name__}: {e}")
        return _fallback(query)
    return result if isinstance(result, Intent) else _fallback(query)
