"""FastAPI app: serves the chat UI and exposes the chat + conversation API.

On Windows, Playwright (used by browser-use) requires the Proactor event loop to
spawn subprocesses. We set that policy at import time, before uvicorn starts its loop.
"""

import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from . import config, memory  # noqa: E402
from .agent import run_turn  # noqa: E402
from .memory import MemoryStore  # noqa: E402
from .scheduler import Scheduler, initial_next_run  # noqa: E402
from .storage import Storage  # noqa: E402
from .stt import Transcriber  # noqa: E402

store = Storage()

# Long-term memory shares the same SQLite store; register it so tools can reach it.
memory_store = MemoryStore(store)
memory.set_active(memory_store)

# Proactive scheduler runs due tasks in the background for the server's lifetime.
scheduler = Scheduler(store)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(title="Oli", lifespan=lifespan)


# --- request models ------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class RenameRequest(BaseModel):
    title: str


# --- conversation endpoints ----------------------------------------------

@app.get("/api/conversations")
def list_conversations():
    return store.list_conversations()


@app.post("/api/conversations")
def create_conversation():
    cid = store.create_conversation()
    return {"id": cid, "title": "New chat"}


@app.get("/api/conversations/{cid}")
def get_conversation(cid: str):
    if not store.conversation_exists(cid):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"id": cid, "messages": store.get_messages(cid)}


@app.patch("/api/conversations/{cid}")
def rename_conversation(cid: str, req: RenameRequest):
    if not store.conversation_exists(cid):
        raise HTTPException(status_code=404, detail="Conversation not found")
    store.rename_conversation(cid, req.title)
    return {"ok": True}


@app.delete("/api/conversations/{cid}")
def delete_conversation(cid: str):
    store.delete_conversation(cid)
    return {"ok": True}


# --- memory endpoints ----------------------------------------------------

class MemoryRequest(BaseModel):
    content: str


@app.get("/api/memories")
def list_memories():
    return store.list_memories()


@app.post("/api/memories")
def add_memory(req: MemoryRequest):
    result = memory_store.remember(req.content, source="explicit")
    return result


@app.delete("/api/memories/{mid}")
def delete_memory(mid: str):
    store.delete_memory(mid)
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
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


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
    if not cid or not store.conversation_exists(cid):
        cid = store.create_conversation()

    # Auto-title a fresh conversation from its first user message.
    existing = store.get_messages(cid)
    if not existing:
        title = message[:60] + ("…" if len(message) > 60 else "")
        store.rename_conversation(cid, title)

    async def event_stream():
        # Tell the client which conversation this stream belongs to.
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

class ScheduledTaskRequest(BaseModel):
    title: str
    prompt: str
    schedule_kind: str  # 'interval' | 'daily'
    interval_sec: int | None = None
    time_of_day: str | None = None  # 'HH:MM'


@app.get("/api/tasks")
def list_tasks():
    return store.list_scheduled_tasks()


@app.post("/api/tasks")
def create_task(req: ScheduledTaskRequest):
    if req.schedule_kind not in ("interval", "daily"):
        raise HTTPException(status_code=400, detail="schedule_kind must be 'interval' or 'daily'")
    now = time.time()
    next_run = initial_next_run(
        req.schedule_kind, now, req.interval_sec, req.time_of_day
    )
    tid = store.add_scheduled_task(
        title=req.title,
        prompt=req.prompt,
        schedule_kind=req.schedule_kind,
        next_run=next_run,
        interval_sec=req.interval_sec,
        time_of_day=req.time_of_day,
    )
    return {"id": tid, "next_run": next_run}


@app.post("/api/tasks/{tid}/toggle")
def toggle_task(tid: str, enabled: bool):
    if not store.get_scheduled_task(tid):
        raise HTTPException(status_code=404, detail="Task not found")
    store.set_task_enabled(tid, enabled)
    return {"ok": True}


@app.post("/api/tasks/{tid}/run")
async def run_task_now(tid: str):
    task = store.get_scheduled_task(tid)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    result = await scheduler.run_task(task, scheduled=False)
    return result


@app.delete("/api/tasks/{tid}")
def delete_task(tid: str):
    store.delete_scheduled_task(tid)
    return {"ok": True}


# --- proactive: notifications -------------------------------------------

@app.get("/api/notifications")
def list_notifications():
    return {
        "unread": store.unread_count(),
        "items": store.list_notifications(),
    }


@app.post("/api/notifications/read")
def mark_read():
    store.mark_notifications_read()
    return {"ok": True}


@app.delete("/api/notifications/{nid}")
def delete_notification(nid: str):
    store.delete_notification(nid)
    return {"ok": True}


# --- static UI (mounted last so it doesn't shadow /api) -------------------

@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(config.WEB_DIR)), name="web")
