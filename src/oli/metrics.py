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


def render() -> tuple[bytes, str]:
    """Return (payload, content_type) for the /metrics endpoint."""
    return generate_latest(), CONTENT_TYPE_LATEST
