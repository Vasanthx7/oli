"""Prometheus metrics.

Exposes app + agent metrics at /metrics for scraping. Labels are kept
low-cardinality on purpose (method/status, tool name) so the series count stays
bounded — no per-URL or per-id labels.
"""

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

REQUEST_COUNT = Counter("oli_http_requests_total", "Total HTTP requests", ["method", "status"])
REQUEST_LATENCY = Histogram(
    "oli_http_request_duration_seconds", "HTTP request latency in seconds", ["method"]
)
CHAT_TURNS = Counter("oli_chat_turns_total", "Chat turns started")
TOOL_CALLS = Counter("oli_tool_calls_total", "Agent tool calls executed", ["tool"])

# LLM cost/perf. `kind` is prompt|completion; `model` is the model id (bounded — one
# per configured tier, so cardinality stays low). Populated per chat-model call.
LLM_TOKENS = Counter("oli_llm_tokens_total", "LLM tokens used", ["kind", "model"])
LLM_CALL_LATENCY = Histogram(
    "oli_llm_call_duration_seconds", "LLM call latency in seconds", ["model"]
)
# Agent turn failures, keyed by exception class name (low-cardinality taxonomy).
AGENT_ERRORS = Counter("oli_agent_errors_total", "Agent turn errors", ["type"])


def render() -> tuple[bytes, str]:
    """Return (payload, content_type) for the /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
