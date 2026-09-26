"""HTTP Basic Auth for the whole app — the single trust boundary.

Oli is a single-user app (ADR 0008) but is deployed on the public internet
(ADR 0012). Without this, any anonymous request could drive the agent, spend LLM
quota, read/delete data, and — worst — use the ``browse`` tool against the
owner's *authenticated* browser profiles. This middleware requires valid Basic
credentials on every request before it reaches any route.

It is a pure-ASGI middleware (not ``@app.middleware("http")``) on purpose: the
decorator form only sees the ``http`` scope, so the ``/api/live/ws`` WebSocket
handshake would bypass it. This guards ``http`` *and* ``websocket`` scopes.

Auth is enabled only when ``AUTH_PASSWORD`` is set, so tests/CI and a purely
local dev run stay open (and offline). Startup logs a warning if the app runs in
production without it — see ``main.lifespan``.
"""

import base64
import hmac

from starlette.types import ASGIApp, Receive, Scope, Send

# Probes hit these without credentials (docker/orchestration liveness+readiness),
# and they expose nothing sensitive, so they stay open even when auth is enabled.
_EXEMPT_PATHS = frozenset({"/health", "/health/ready"})


class BasicAuthMiddleware:
    """Reject any http/websocket request lacking valid Basic credentials."""

    def __init__(self, app: ASGIApp, username: str, password: str) -> None:
        self.app = app
        self._username = username
        self._password = password

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or scope.get("path") in _EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        if self._authorized(scope):
            await self.app(scope, receive, send)
            return
        await self._reject(scope, send)

    def _authorized(self, scope: Scope) -> bool:
        headers = dict(scope.get("headers") or [])
        raw = headers.get(b"authorization")
        if not raw:
            return False
        try:
            scheme, _, credentials = raw.decode("latin-1").partition(" ")
            if scheme.lower() != "basic":
                return False
            user, _, pw = base64.b64decode(credentials).decode("utf-8").partition(":")
        except Exception:  # noqa: BLE001 — any malformed header is simply unauthorized
            return False
        # Constant-time compares; check both halves to avoid short-circuit timing.
        user_ok = hmac.compare_digest(user, self._username)
        pw_ok = hmac.compare_digest(pw, self._password)
        return user_ok and pw_ok

    async def _reject(self, scope: Scope, send: Send) -> None:
        if scope["type"] == "websocket":
            # Closing before accept makes the server answer the handshake with 403.
            await send({"type": "websocket.close", "code": 1008})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"www-authenticate", b'Basic realm="Oli"'),
                    (b"content-type", b"text/plain; charset=utf-8"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"Unauthorized"})
