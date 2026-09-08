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
from fastapi import FastAPI, File, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, Response, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import config, memory, metrics  # noqa: E402
from .agent import run_turn  # noqa: E402
from .db import dispose_engine, init_models  # noqa: E402
from .logging_config import configure_logging, get_logger  # noqa: E402
from .memory import MemoryStore  # noqa: E402
from .scheduler import Scheduler, initial_next_run  # noqa: E402
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

# Proactive scheduler runs due tasks in the background for the server's lifetime.
scheduler = Scheduler(store)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # On SQLite (dev/test) create tables directly; production uses Alembic migrations.
    if not config.settings.is_postgres:
        await init_models()
    log.info("startup", environment=config.settings.environment, model=config.settings.groq_model)
    scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()
        await dispose_engine()
        log.info("shutdown")


app = FastAPI(title="Oli", lifespan=lifespan)


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


class ScheduledTaskRequest(BaseModel):
    title: str
    prompt: str
    schedule_kind: str  # 'interval' | 'daily'
    interval_sec: int | None = None
    time_of_day: str | None = None  # 'HH:MM'


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


# --- proactive: scheduled tasks -----------------------------------------


@app.get("/api/tasks")
async def list_tasks():
    return await store.list_scheduled_tasks()


@app.post("/api/tasks")
async def create_task(req: ScheduledTaskRequest):
    if req.schedule_kind not in ("interval", "daily"):
        raise HTTPException(status_code=400, detail="schedule_kind must be 'interval' or 'daily'")
    now = time.time()
    next_run = initial_next_run(req.schedule_kind, now, req.interval_sec, req.time_of_day)
    tid = await store.add_scheduled_task(
        title=req.title,
        prompt=req.prompt,
        schedule_kind=req.schedule_kind,
        next_run=next_run,
        interval_sec=req.interval_sec,
        time_of_day=req.time_of_day,
    )
    return {"id": tid, "next_run": next_run}


@app.post("/api/tasks/{tid}/toggle")
async def toggle_task(tid: str, enabled: bool):
    if not await store.get_scheduled_task(tid):
        raise HTTPException(status_code=404, detail="Task not found")
    await store.set_task_enabled(tid, enabled)
    return {"ok": True}


@app.post("/api/tasks/{tid}/run")
async def run_task_now(tid: str):
    task = await store.get_scheduled_task(tid)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return await scheduler.run_task(task, scheduled=False)


@app.delete("/api/tasks/{tid}")
async def delete_task(tid: str):
    await store.delete_scheduled_task(tid)
    return {"ok": True}


# --- proactive: notifications -------------------------------------------


@app.get("/api/notifications")
async def list_notifications():
    return {
        "unread": await store.unread_count(),
        "items": await store.list_notifications(),
    }


@app.post("/api/notifications/read")
async def mark_read():
    await store.mark_notifications_read()
    return {"ok": True}


@app.delete("/api/notifications/{nid}")
async def delete_notification(nid: str):
    await store.delete_notification(nid)
    return {"ok": True}


# --- static UI (mounted last so it doesn't shadow /api) -------------------


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(config.WEB_DIR)), name="web")
