"""SQLite persistence for conversations and messages.

One database file (data/assistant.db). A single connection is shared across the
async app with a lock, since SQLite writes must be serialized. Message content
is stored as-is; tool activity is recorded as messages with role='tool' so the
full turn can be replayed.
"""

import sqlite3
import threading
import time
import uuid
from typing import Optional

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT 'New chat',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,            -- 'user' | 'assistant' | 'tool'
    content         TEXT NOT NULL DEFAULT '',
    tool_name       TEXT,                     -- set when role='tool'
    created_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS memories (
    id         TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    embedding  BLOB NOT NULL,            -- float32 vector bytes
    source     TEXT NOT NULL DEFAULT 'auto',  -- 'auto' | 'explicit'
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    schedule_kind TEXT NOT NULL,            -- 'interval' | 'daily'
    interval_sec  INTEGER,                  -- for 'interval'
    time_of_day   TEXT,                     -- 'HH:MM' local, for 'daily'
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_run      REAL,
    next_run      REAL NOT NULL,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id         TEXT PRIMARY KEY,
    task_id    TEXT,
    title      TEXT NOT NULL,
    content    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'ok',  -- 'ok' | 'error'
    read       INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_created ON notifications(created_at);
"""


class Storage:
    def __init__(self, db_path=DB_PATH):
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    # --- conversations ---------------------------------------------------

    def create_conversation(self, title: str = "New chat") -> str:
        cid = uuid.uuid4().hex
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (cid, title, now, now),
            )
            self._conn.commit()
        return cid

    def conversation_exists(self, cid: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (cid,)
            ).fetchone()
        return row is not None

    def list_conversations(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, title, created_at, updated_at FROM conversations "
                "ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def rename_conversation(self, cid: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
                (title, time.time(), cid),
            )
            self._conn.commit()

    def delete_conversation(self, cid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))
            self._conn.commit()

    def _touch(self, cid: str) -> None:
        self._conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?", (time.time(), cid)
        )

    # --- messages --------------------------------------------------------

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        tool_name: Optional[str] = None,
    ) -> str:
        mid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, tool_name, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (mid, conversation_id, role, content, tool_name, time.time()),
            )
            self._touch(conversation_id)
            self._conn.commit()
        return mid

    def get_messages(self, conversation_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content, tool_name, created_at FROM messages "
                "WHERE conversation_id = ? ORDER BY created_at ASC",
                (conversation_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- memories --------------------------------------------------------

    def add_memory(self, content: str, embedding: bytes, source: str = "auto") -> str:
        mid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories (id, content, embedding, source, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (mid, content, embedding, source, time.time()),
            )
            self._conn.commit()
        return mid

    def get_memories(self) -> list[dict]:
        """Return all memories including raw embedding bytes (for similarity search)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, content, embedding, source, created_at FROM memories "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def list_memories(self) -> list[dict]:
        """Return memories without embedding bytes (for API/UI display)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, content, source, created_at FROM memories "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_memory(self, mid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM memories WHERE id = ?", (mid,))
            self._conn.commit()

    # --- scheduled tasks -------------------------------------------------

    def add_scheduled_task(
        self,
        title: str,
        prompt: str,
        schedule_kind: str,
        next_run: float,
        interval_sec: int | None = None,
        time_of_day: str | None = None,
    ) -> str:
        tid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO scheduled_tasks (id, title, prompt, schedule_kind, interval_sec, "
                "time_of_day, enabled, last_run, next_run, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)",
                (tid, title, prompt, schedule_kind, interval_sec, time_of_day,
                 next_run, time.time()),
            )
            self._conn.commit()
        return tid

    def list_scheduled_tasks(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_scheduled_task(self, tid: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id = ?", (tid,)
            ).fetchone()
        return dict(row) if row else None

    def get_due_tasks(self, now: float) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scheduled_tasks WHERE enabled = 1 AND next_run <= ?",
                (now,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_task_schedule(self, tid: str, last_run: float, next_run: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE scheduled_tasks SET last_run = ?, next_run = ? WHERE id = ?",
                (last_run, next_run, tid),
            )
            self._conn.commit()

    def set_task_enabled(self, tid: str, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE scheduled_tasks SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, tid),
            )
            self._conn.commit()

    def delete_scheduled_task(self, tid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM scheduled_tasks WHERE id = ?", (tid,))
            self._conn.commit()

    # --- notifications ---------------------------------------------------

    def add_notification(
        self, title: str, content: str, task_id: str | None = None, status: str = "ok"
    ) -> str:
        nid = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO notifications (id, task_id, title, content, status, read, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?)",
                (nid, task_id, title, content, status, time.time()),
            )
            self._conn.commit()
        return nid

    def list_notifications(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM notifications ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def unread_count(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM notifications WHERE read = 0"
            ).fetchone()
        return int(row["c"])

    def mark_notifications_read(self) -> None:
        with self._lock:
            self._conn.execute("UPDATE notifications SET read = 1 WHERE read = 0")
            self._conn.commit()

    def delete_notification(self, nid: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM notifications WHERE id = ?", (nid,))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
