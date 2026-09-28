"""HTTP Basic Auth middleware — the app-wide trust boundary (offline)."""

import base64

import httpx
import pytest_asyncio
from fastapi import FastAPI

from oli.auth import BasicAuthMiddleware

_USER, _PASS = "oli", "s3cret"


def _basic(user: str, pw: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest_asyncio.fixture
async def auth_client():
    app = FastAPI()
    app.add_middleware(BasicAuthMiddleware, username=_USER, password=_PASS)

    @app.get("/api/secret")
    async def secret():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_rejects_missing_credentials(auth_client):
    r = await auth_client.get("/api/secret")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith("Basic")


async def test_rejects_wrong_credentials(auth_client):
    r = await auth_client.get("/api/secret", headers=_basic(_USER, "wrong"))
    assert r.status_code == 401


async def test_accepts_correct_credentials(auth_client):
    r = await auth_client.get("/api/secret", headers=_basic(_USER, _PASS))
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_health_exempt_from_auth(auth_client):
    r = await auth_client.get("/health")
    assert r.status_code == 200


async def test_websocket_handshake_rejected_without_credentials():
    # WebSocket scope isn't handled by @app.middleware("http"), so verify the
    # pure-ASGI guard closes the handshake before it reaches the inner app.
    called = False

    async def inner(scope, receive, send):
        nonlocal called
        called = True

    mw = BasicAuthMiddleware(inner, username=_USER, password=_PASS)
    scope = {"type": "websocket", "path": "/api/live/ws", "headers": []}
    sent: list[dict] = []

    async def send(msg):
        sent.append(msg)

    async def receive():
        return {"type": "websocket.connect"}

    await mw(scope, receive, send)
    assert not called
    assert sent and sent[0]["type"] == "websocket.close"


async def test_websocket_handshake_allowed_with_credentials():
    called = False

    async def inner(scope, receive, send):
        nonlocal called
        called = True

    mw = BasicAuthMiddleware(inner, username=_USER, password=_PASS)
    token = base64.b64encode(f"{_USER}:{_PASS}".encode()).decode()
    scope = {
        "type": "websocket",
        "path": "/api/live/ws",
        "headers": [(b"authorization", f"Basic {token}".encode())],
    }

    async def send(msg): ...

    async def receive():
        return {"type": "websocket.connect"}

    await mw(scope, receive, send)
    assert called
