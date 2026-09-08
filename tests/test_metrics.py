"""The /metrics endpoint exposes Prometheus metrics and reflects request activity."""


async def test_metrics_endpoint_exposes_prometheus(client):
    # Generate some request activity first.
    await client.get("/api/conversations")

    r = await client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    # Our custom metric families are present.
    assert "oli_http_requests_total" in body
    assert "oli_chat_turns_total" in body
    assert "oli_tool_calls_total" in body
