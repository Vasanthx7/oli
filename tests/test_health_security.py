"""Health checks and security hardening (offline)."""


async def test_health_liveness(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_health_readiness_ok(client):
    # DB is reachable in tests, so readiness should pass.
    r = await client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


async def test_security_headers_present(client):
    r = await client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"


async def test_oversized_request_rejected(client):
    # A body beyond the cap is rejected (413) by the middleware before routing.
    big = b"x" * (26 * 1024 * 1024)
    r = await client.post("/api/chat", content=big)
    assert r.status_code == 413
