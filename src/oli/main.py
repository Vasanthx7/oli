"""FastAPI app: serves the chat UI and exposes the chat + conversation API.

On Windows, Playwright (used by browser-use) requires the Proactor event loop to
spawn subprocesses. We set that policy at import time, before uvicorn starts its loop.
"""

import asyncio
import json
import sys
import time
import uuid
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import structlog  # noqa: E402
from fastapi import (  # noqa: E402
    FastAPI,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import (  # noqa: E402
    FileResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, field_validator  # noqa: E402

from . import config, live_browser, memory, metrics, profiles, tts  # noqa: E402
from .agent import run_turn  # noqa: E402
from .auth import BasicAuthMiddleware  # noqa: E402
from .db import dispose_engine, init_models  # noqa: E402
from .logging_config import configure_logging, get_logger  # noqa: E402
from .memory import MemoryStore  # noqa: E402
from .net_guard import UnsafeURLError, validate_url  # noqa: E402
from .profiles import ProfileManager  # noqa: E402
from .storage import Storage  # noqa: E402
from .stt import Transcriber  # noqa: E402
from .tracing import configure_tracing  # noqa: E402

configure_logging()
configure_tracing()
log = get_logger(__name__)

store = Storage()

# Long-term memory shares the same store; register it so tools can reach it.
memory_store = MemoryStore(store)
memory.set_active(memory_store)

# Persistent browser profiles (authenticated sessions the browse tool can reuse).
profile_manager = ProfileManager(store)
profiles.set_active(profile_manager)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # On SQLite (dev/test) create tables directly; production uses Alembic migrations.
    if not config.settings.is_postgres:
        await init_models()
    log.info(
        "startup",
        environment=config.settings.environment,
        model=config.settings.groq_model,
        auth_enabled=config.settings.auth_enabled,
    )
    if config.settings.environment == "production" and not config.settings.auth_enabled:
        log.warning(
            "auth_disabled_in_production",
            detail="AUTH_PASSWORD is not set — every endpoint is publicly reachable. "
            "Set a strong AUTH_PASSWORD before exposing this app to the internet.",
        )
    try:
        yield
    finally:
        await profile_manager.shutdown()
        await live_browser.session().stop()
        await dispose_engine()
        log.info("shutdown")


app = FastAPI(title="Oli", lifespan=lifespan)

# Gate the entire app (HTTP + WebSocket) behind Basic Auth when a password is set.
# Added last so it wraps outermost — unauthorized requests are rejected before they
# reach any other middleware or route. No-op when AUTH_PASSWORD is empty (dev/test).
if config.settings.auth_enabled:
    app.add_middleware(
        BasicAuthMiddleware,
        username=config.settings.auth_username,
        password=config.settings.auth_password,
    )

# Cap request bodies (audio uploads are the largest legitimate payload).
MAX_REQUEST_BYTES = 25 * 1024 * 1024


@app.middleware("http")
async def security_and_limits(request: Request, call_next):
    """Reject oversized requests and add baseline security headers to every response."""
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BYTES:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Bind a request id to the log context and record request metrics."""
    request_id = request.headers.get("x-request-id", uuid.uuid4().hex[:12])
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)

    start = time.perf_counter()
    response = await call_next(request)
    metrics.REQUEST_LATENCY.labels(method=request.method).observe(time.perf_counter() - start)
    metrics.REQUEST_COUNT.labels(method=request.method, status=response.status_code).inc()

    response.headers["x-request-id"] = request_id
    return response


@app.get("/metrics")
def prometheus_metrics():
    payload, content_type = metrics.render()
    return Response(content=payload, media_type=content_type)


# --- request models ------------------------------------------------------


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class RenameRequest(BaseModel):
    title: str


class MemoryRequest(BaseModel):
    content: str


class ProfileRequest(BaseModel):
    label: str
    start_url: str = ""

    @field_validator("label")
    @classmethod
    def _label_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("label must not be empty")
        return v

    @field_validator("start_url")
    @classmethod
    def _url_is_http(cls, v: str) -> str:
        # The login window navigates here, so only allow http(s) (or nothing) —
        # never file:/javascript:/data: which could read local files or run script.
        v = v.strip()
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("start_url must be an http(s) URL")
        return v


class LiveStartRequest(BaseModel):
    profile: str | None = None
    url: str | None = None


class LiveNavigateRequest(BaseModel):
    url: str


# --- conversation endpoints ----------------------------------------------


@app.get("/api/conversations")
async def list_conversations():
    return await store.list_conversations()


@app.post("/api/conversations")
async def create_conversation():
    cid = await store.create_conversation()
    return {"id": cid, "title": "New chat"}


@app.get("/api/conversations/{cid}")
async def get_conversation(cid: str):
    if not await store.conversation_exists(cid):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": cid, "messages": await store.get_messages(cid)}


@app.patch("/api/conversations/{cid}")
async def rename_conversation(cid: str, req: RenameRequest):
    if not await store.conversation_exists(cid):
        raise HTTPException(status_code=404, detail="Conversation not found")
    await store.rename_conversation(cid, req.title)
    return {"ok": True}


@app.delete("/api/conversations/{cid}")
async def delete_conversation(cid: str):
    await store.delete_conversation(cid)
    return {"ok": True}


# --- memory endpoints ----------------------------------------------------


@app.get("/api/memories")
async def list_memories():
    return await store.list_memories()


@app.post("/api/memories")
async def add_memory(req: MemoryRequest):
    return await memory_store.remember(req.content, source="explicit")


@app.delete("/api/memories/{mid}")
async def delete_memory(mid: str):
    await store.delete_memory(mid)
    return {"ok": True}


# --- voice: speech-to-text ----------------------------------------------


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...)):
    data = await audio.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio")
    try:
        transcriber = Transcriber()
        text = await transcriber.transcribe(data, filename=audio.filename or "audio.webm")
        return {"text": text}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


# --- voice: text-to-speech ----------------------------------------------

_TTS_MEDIA = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac", "ogg": "audio/ogg"}


class TtsRequest(BaseModel):
    text: str
    voice: str | None = None


@app.get("/api/tts/voices")
async def tts_voice_list():
    """Whether server TTS is on + the provider's voice names (for the frontend picker)."""
    return {"enabled": tts.enabled(), "voices": tts.voices(), "default": config.settings.tts_voice}


@app.post("/api/tts")
async def tts_synthesize(req: TtsRequest):
    """Synthesize reply text to audio. 503 when disabled/failed so the client falls back
    to the browser voice — TTS must never block a reply."""
    if not tts.enabled():
        raise HTTPException(status_code=503, detail="Server TTS is disabled")
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")
    try:
        audio = await tts.Synthesizer().synthesize(text, req.voice)
    except Exception as e:  # noqa: BLE001 — surface as 503 so the client falls back
        raise HTTPException(status_code=503, detail=f"{type(e).__name__}: {e}") from e
    fmt = config.settings.tts_format
    return Response(content=audio, media_type=_TTS_MEDIA.get(fmt, f"audio/{fmt}"))


# --- chat (SSE) ----------------------------------------------------------


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.post("/api/chat")
async def chat(req: ChatRequest):
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    # Create a conversation on the fly if none was supplied.
    cid = req.conversation_id
    if not cid or not await store.conversation_exists(cid):
        cid = await store.create_conversation()

    # Auto-title a fresh conversation from its first user message.
    existing = await store.get_messages(cid)
    if not existing:
        title = message[:60] + ("…" if len(message) > 60 else "")
        await store.rename_conversation(cid, title)

    async def event_stream():
        yield _sse({"type": "meta", "conversation_id": cid})
        try:
            async for event in run_turn(store, cid, message):
                yield _sse(event)
        except Exception as e:  # noqa: BLE001
            yield _sse({"type": "error", "message": f"{type(e).__name__}: {e}"})
        yield _sse({"type": "end"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- browser profiles ----------------------------------------------------


@app.get("/api/profiles")
async def list_profiles():
    return await profile_manager.list_with_status()


@app.post("/api/profiles")
async def create_profile(req: ProfileRequest):
    try:
        return await profile_manager.create(req.label, req.start_url)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@app.post("/api/profiles/{name}/login")
async def start_profile_login(name: str):
    """Open a visible browser window so the user can log in once."""
    try:
        await profile_manager.start_login(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        # e.g. no display on a headless host.
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"ok": True, "status": "window_open"}


@app.post("/api/profiles/{name}/login/finish")
async def finish_profile_login(name: str):
    """Close the login window, persisting the authenticated session to disk."""
    return await profile_manager.finish_login(name)


@app.delete("/api/profiles/{name}")
async def delete_profile(name: str):
    await profile_manager.delete(name)
    return {"ok": True}


# --- live browser (watch + drive a real browser) -------------------------


@app.get("/api/live/status")
async def live_status():
    return live_browser.session().status()


@app.post("/api/live/start")
async def live_start(req: LiveStartRequest):
    """Launch the live browser (optionally bound to a profile) and start streaming."""
    if live_browser.session().agent_mode:
        raise HTTPException(
            status_code=409,
            detail="Oli is browsing right now — watch or take control instead of "
            "starting a new session.",
        )
    try:
        await live_browser.session().start(profile=req.profile, url=req.url)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e
    return live_browser.session().status()


@app.post("/api/live/control")
async def live_control():
    """Take control of a watched agent run: pause the agent, enable user input."""
    ok = await live_browser.session().take_control()
    return {"ok": ok, **live_browser.session().status()}


@app.post("/api/live/release")
async def live_release():
    """Hand control back to the agent and resume the run."""
    ok = await live_browser.session().release_control()
    return {"ok": ok, **live_browser.session().status()}


@app.post("/api/live/navigate")
async def live_navigate(req: LiveNavigateRequest):
    # Only http(s) to a public host — never file:// or an internal/metadata address.
    try:
        target = await asyncio.to_thread(validate_url, req.url)
    except UnsafeURLError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await live_browser.session().navigate(target)
    return live_browser.session().status()


@app.post("/api/live/stop")
async def live_stop():
    """Stop the live browser; if it was bound to a profile, stamp the login time."""
    profile = await live_browser.session().stop()
    if profile:
        # The persistent context flushed cookies on close — record the login.
        await store.touch_profile_login(profile, time.time())
    return {"ok": True, "profile": profile}


@app.websocket("/api/live/ws")
async def live_ws(ws: WebSocket):
    """Bi-directional stream: JPEG frames out, mouse/keyboard events in."""
    await ws.accept()
    sess = live_browser.session()
    queue = sess.subscribe()

    async def pump_frames() -> None:
        while True:
            frame = await queue.get()
            if frame is None:  # session stopped
                break
            await ws.send_json({"type": "frame", "data": frame})

    async def pump_input() -> None:
        while True:
            event = await ws.receive_json()
            await sess.dispatch(event)

    frames_task = asyncio.create_task(pump_frames())
    input_task = asyncio.create_task(pump_input())
    try:
        done, pending = await asyncio.wait(
            {frames_task, input_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        frames_task.cancel()
        input_task.cancel()
        sess.unsubscribe(queue)


# --- health checks -------------------------------------------------------


@app.get("/health")
def health():
    """Liveness: the process is up and serving."""
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready():
    """Readiness: the database is reachable. 503 if not (for orchestration probes)."""
    from sqlalchemy import text

    from .db import get_sessionmaker

    try:
        async with get_sessionmaker()() as s:
            await s.execute(text("SELECT 1"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"database unavailable: {e}") from e
    return {"status": "ready"}


# --- static UI (mounted last so it doesn't shadow /api) -------------------


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(config.WEB_DIR)), name="web")
