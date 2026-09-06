"""Async data-access layer (repository) over SQLAlchemy.

Keeps the same method names and dict-returning shape the rest of the app already
uses, but every method is now async and backed by SQLAlchemy — so the same code
runs on SQLite (dev/test) and PostgreSQL (production) by swapping DATABASE_URL.
"""

import time
import uuid

from sqlalchemy import delete, func, select, update

from .db import get_sessionmaker
from .models import Conversation, Memory, Message, Notification, ScheduledTask


def _conversation_dict(c: Conversation) -> dict:
    return {"id": c.id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at}


def _message_dict(m: Message) -> dict:
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "tool_name": m.tool_name,
        "created_at": m.created_at,
    }


def _task_dict(t: ScheduledTask) -> dict:
    return {
        "id": t.id,
        "title": t.title,
        "prompt": t.prompt,
        "schedule_kind": t.schedule_kind,
        "interval_sec": t.interval_sec,
        "time_of_day": t.time_of_day,
        "enabled": int(t.enabled),
        "last_run": t.last_run,
        "next_run": t.next_run,
        "created_at": t.created_at,
    }


def _notification_dict(n: Notification) -> dict:
    return {
        "id": n.id,
        "task_id": n.task_id,
        "title": n.title,
        "content": n.content,
        "status": n.status,
        "read": int(n.read),
        "created_at": n.created_at,
    }


class Storage:
    """Async repository. Resolves the sessionmaker lazily per call so it always
    uses the current engine (important for tests that recreate the engine)."""

    @property
    def _sm(self):
        return get_sessionmaker()

    # --- conversations ---------------------------------------------------

    async def create_conversation(self, title: str = "New chat") -> str:
        async with self._sm() as s:
            conv = Conversation(id=uuid.uuid4().hex, title=title)
            s.add(conv)
            await s.commit()
            return conv.id

    async def ensure_conversation(self, cid: str, title: str) -> str:
        """Create a conversation with a specific id if it doesn't already exist."""
        async with self._sm() as s:
            existing = await s.get(Conversation, cid)
            if existing is None:
                s.add(Conversation(id=cid, title=title))
                await s.commit()
            return cid

    async def conversation_exists(self, cid: str) -> bool:
        async with self._sm() as s:
            return await s.get(Conversation, cid) is not None

    async def list_conversations(self) -> list[dict]:
        async with self._sm() as s:
            rows = (
                await s.execute(select(Conversation).order_by(Conversation.updated_at.desc()))
            ).scalars()
            return [_conversation_dict(c) for c in rows]

    async def rename_conversation(self, cid: str, title: str) -> None:
        async with self._sm() as s:
            await s.execute(
                update(Conversation)
                .where(Conversation.id == cid)
                .values(title=title, updated_at=time.time())
            )
            await s.commit()

    async def delete_conversation(self, cid: str) -> None:
        async with self._sm() as s:
            await s.execute(delete(Conversation).where(Conversation.id == cid))
            await s.commit()

    # --- messages --------------------------------------------------------

    async def add_message(
        self, conversation_id: str, role: str, content: str, tool_name: str | None = None
    ) -> str:
        async with self._sm() as s:
            msg = Message(
                id=uuid.uuid4().hex,
                conversation_id=conversation_id,
                role=role,
                content=content,
                tool_name=tool_name,
            )
            s.add(msg)
            await s.execute(
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=time.time())
            )
            await s.commit()
            return msg.id

    async def get_messages(self, conversation_id: str) -> list[dict]:
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.asc())
                )
            ).scalars()
            return [_message_dict(m) for m in rows]

    # --- memories --------------------------------------------------------

    async def add_memory(self, content: str, embedding: bytes, source: str = "auto") -> str:
        async with self._sm() as s:
            mem = Memory(id=uuid.uuid4().hex, content=content, embedding=embedding, source=source)
            s.add(mem)
            await s.commit()
            return mem.id

    async def get_memories(self) -> list[dict]:
        """All memories including raw embedding bytes (for similarity search)."""
        async with self._sm() as s:
            rows = (await s.execute(select(Memory).order_by(Memory.created_at.desc()))).scalars()
            return [
                {"id": m.id, "content": m.content, "embedding": m.embedding, "source": m.source}
                for m in rows
            ]

    async def list_memories(self) -> list[dict]:
        """Memories without embedding bytes (for API/UI display)."""
        async with self._sm() as s:
            rows = (await s.execute(select(Memory).order_by(Memory.created_at.desc()))).scalars()
            return [
                {"id": m.id, "content": m.content, "source": m.source, "created_at": m.created_at}
                for m in rows
            ]

    async def delete_memory(self, mid: str) -> None:
        async with self._sm() as s:
            await s.execute(delete(Memory).where(Memory.id == mid))
            await s.commit()

    # --- scheduled tasks -------------------------------------------------

    async def add_scheduled_task(
        self,
        title: str,
        prompt: str,
        schedule_kind: str,
        next_run: float,
        interval_sec: int | None = None,
        time_of_day: str | None = None,
    ) -> str:
        async with self._sm() as s:
            task = ScheduledTask(
                id=uuid.uuid4().hex,
                title=title,
                prompt=prompt,
                schedule_kind=schedule_kind,
                interval_sec=interval_sec,
                time_of_day=time_of_day,
                enabled=1,
                next_run=next_run,
            )
            s.add(task)
            await s.commit()
            return task.id

    async def list_scheduled_tasks(self) -> list[dict]:
        async with self._sm() as s:
            rows = (
                await s.execute(select(ScheduledTask).order_by(ScheduledTask.created_at.desc()))
            ).scalars()
            return [_task_dict(t) for t in rows]

    async def get_scheduled_task(self, tid: str) -> dict | None:
        async with self._sm() as s:
            t = await s.get(ScheduledTask, tid)
            return _task_dict(t) if t else None

    async def get_due_tasks(self, now: float) -> list[dict]:
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(ScheduledTask).where(
                        ScheduledTask.enabled == 1, ScheduledTask.next_run <= now
                    )
                )
            ).scalars()
            return [_task_dict(t) for t in rows]

    async def update_task_schedule(self, tid: str, last_run: float, next_run: float) -> None:
        async with self._sm() as s:
            await s.execute(
                update(ScheduledTask)
                .where(ScheduledTask.id == tid)
                .values(last_run=last_run, next_run=next_run)
            )
            await s.commit()

    async def set_task_enabled(self, tid: str, enabled: bool) -> None:
        async with self._sm() as s:
            await s.execute(
                update(ScheduledTask)
                .where(ScheduledTask.id == tid)
                .values(enabled=1 if enabled else 0)
            )
            await s.commit()

    async def delete_scheduled_task(self, tid: str) -> None:
        async with self._sm() as s:
            await s.execute(delete(ScheduledTask).where(ScheduledTask.id == tid))
            await s.commit()

    # --- notifications ---------------------------------------------------

    async def add_notification(
        self, title: str, content: str, task_id: str | None = None, status: str = "ok"
    ) -> str:
        async with self._sm() as s:
            n = Notification(
                id=uuid.uuid4().hex, task_id=task_id, title=title, content=content, status=status
            )
            s.add(n)
            await s.commit()
            return n.id

    async def list_notifications(self, limit: int = 50) -> list[dict]:
        async with self._sm() as s:
            rows = (
                await s.execute(
                    select(Notification).order_by(Notification.created_at.desc()).limit(limit)
                )
            ).scalars()
            return [_notification_dict(n) for n in rows]

    async def unread_count(self) -> int:
        async with self._sm() as s:
            count = await s.scalar(
                select(func.count()).select_from(Notification).where(Notification.read == 0)
            )
            return int(count or 0)

    async def mark_notifications_read(self) -> None:
        async with self._sm() as s:
            await s.execute(update(Notification).where(Notification.read == 0).values(read=1))
            await s.commit()

    async def delete_notification(self, nid: str) -> None:
        async with self._sm() as s:
            await s.execute(delete(Notification).where(Notification.id == nid))
            await s.commit()
