"""Intent classification: a cheap-model pre-pass that tags each turn.

Runs before the agent node in the graph (START -> classify -> agent). Produces a
structured `Intent` stored in graph state, giving downstream stages a signal to
key on: model routing (Phase 3 picks a tier from `complexity`/`needs_tools`) and
observability (the intent is emitted to metrics, traces, and logs).

Classification is best-effort: any failure (bad JSON, endpoint down, no key) falls
back to a safe default so the turn still runs — the agent keeps all its tools
regardless, so a wrong or missing intent only affects routing, never capability.
"""

import asyncio
import time
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from . import jev, metrics, providers
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


class IntentDecision(BaseModel):
    """Jev-compatible view of :class:`Intent` — only the typed decision fields Jev can emit.

    No ``str`` fields (Jev can't produce text): ``normalized_query`` is dropped (it is unused
    downstream) and ``confidence`` comes from Jev's provider_details, not an output field."""

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
    """Lazy singleton: the fast tier over the cloud chain, constrained to emit `Intent`.

    Uses the fast tier — classification is a cheap, high-volume pre-pass, so it should
    never burn the strong model — with the same Groq -> Mistral failover as the agent
    (see :mod:`oli.providers`). Constructed lazily so importing this module needs no API
    key (matches the graph's pattern)."""
    global _classifier
    if _classifier is None:
        _classifier = providers.build_structured_model(Intent, tier="fast", temperature=0)
    return _classifier


async def _classify_llm(query: str) -> Intent:
    """The cloud-LLM classifier path (Groq -> Mistral). Never raises — degrades to chat."""
    try:
        result = await get_classifier().ainvoke(
            [SystemMessage(content=_CLASSIFIER_SYSTEM), HumanMessage(content=query)]
        )
    except Exception as e:  # noqa: BLE001 — classification must never break a turn
        log.warning("intent_classification_failed", error=f"{type(e).__name__}: {e}")
        return _fallback(query)
    return result if isinstance(result, Intent) else _fallback(query)


async def classify_jev(query: str) -> Intent:
    """Classify via Jev (typed decision). Raises on any failure so the caller fails over."""
    prompt = f"{_CLASSIFIER_SYSTEM}\n\nUser message: {query}"
    res = await jev.decide(IntentDecision, prompt)
    d = res.output
    assert isinstance(d, IntentDecision)
    # Report the least-confident field as the overall confidence (conservative).
    confidence = min(res.confidence.values()) if res.confidence else 0.0
    return Intent(
        category=d.category,
        complexity=d.complexity,
        needs_tools=d.needs_tools,
        confidence=confidence,
        normalized_query=query,  # kept for the Intent shape; unused downstream
    )


async def classify(query: str) -> Intent:
    """Classify a single user query into an `Intent`. Never raises — degrades to chat.

    Backend by config: ``use_jev`` routes through Jev (failover to the LLM on any error);
    otherwise the LLM classifies and, when ``jev_shadow`` is on, Jev runs alongside so we
    can compare latency/agreement without affecting behavior."""
    if settings.use_jev:
        t = time.perf_counter()
        try:
            result = await classify_jev(query)
            metrics.DECISION_LATENCY.labels(point="intent", backend="jev").observe(
                time.perf_counter() - t
            )
            return result
        except Exception as e:  # noqa: BLE001 — Jev is early-access; fail over, never break
            log.warning("jev_intent_failover", error=f"{type(e).__name__}: {e}")

    t = time.perf_counter()
    result = await _classify_llm(query)
    metrics.DECISION_LATENCY.labels(point="intent", backend="llm").observe(time.perf_counter() - t)

    if settings.jev_shadow and not settings.use_jev and jev.available():
        _spawn_shadow(query, result)
    return result


# Fire-and-forget shadow comparisons; keep refs so they aren't GC'd mid-flight.
_shadow_tasks: set = set()


def _spawn_shadow(query: str, primary: Intent) -> None:
    task = asyncio.create_task(_shadow_compare(query, primary))
    _shadow_tasks.add(task)
    task.add_done_callback(_shadow_tasks.discard)


async def _shadow_compare(query: str, primary: Intent) -> None:
    """Run Jev alongside the LLM result (unused) and record latency + field agreement."""
    t = time.perf_counter()
    try:
        shadow = await classify_jev(query)
    except Exception as e:  # noqa: BLE001 — shadow must never affect the turn
        log.warning("jev_shadow_failed", error=f"{type(e).__name__}: {e}")
        return
    metrics.DECISION_LATENCY.labels(point="intent", backend="jev").observe(time.perf_counter() - t)
    for field in ("category", "complexity", "needs_tools"):
        agree = "yes" if getattr(primary, field) == getattr(shadow, field) else "no"
        metrics.DECISION_AGREEMENT.labels(point="intent", field=field, agree=agree).inc()
    log.info(
        "jev_shadow",
        category=f"{primary.category}/{shadow.category}",
        complexity=f"{primary.complexity}/{shadow.complexity}",
        needs_tools=f"{primary.needs_tools}/{shadow.needs_tools}",
    )
