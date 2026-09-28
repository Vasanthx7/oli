"""Async data-access layer (repository) over SQLAlchemy.

Keeps the same method names and dict-returning shape the rest of the app already
uses, but every method is now async and backed by SQLAlchemy — so the same code
runs on SQLite (dev/test) and PostgreSQL (production) by swapping DATABASE_URL.
"""

import time
import uuid

from sqlalchemy import delete, select, update

from .db import get_sessionmaker
from .models import (
    BrowserProfile,
    Conversation,
    Memory,
    Message,
)


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


def _profile_dict(p: BrowserProfile) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "label": p.label,
        "start_url": p.start_url,
        "last_login": p.last_login,
        "created_at": p.created_at,
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

    # --- browser profiles ------------------------------------------------

    async def add_profile(self, name: str, label: str, start_url: str) -> str:
        async with self._sm() as s:
            p = BrowserProfile(id=uuid.uuid4().hex, name=name, label=label, start_url=start_url)
            s.add(p)
            await s.commit()
            return p.id

    async def list_profiles(self) -> list[dict]:
        async with self._sm() as s:
            rows = (
                await s.execute(select(BrowserProfile).order_by(BrowserProfile.created_at.asc()))
            ).scalars()
            return [_profile_dict(p) for p in rows]

    async def get_profile_by_name(self, name: str) -> dict | None:
        async with self._sm() as s:
            p = (
                await s.execute(select(BrowserProfile).where(BrowserProfile.name == name))
            ).scalar_one_or_none()
            return _profile_dict(p) if p else None

    async def touch_profile_login(self, name: str, when: float) -> None:
        async with self._sm() as s:
            await s.execute(
                update(BrowserProfile).where(BrowserProfile.name == name).values(last_login=when)
            )
            await s.commit()

    async def delete_profile(self, name: str) -> None:
        async with self._sm() as s:
            await s.execute(delete(BrowserProfile).where(BrowserProfile.name == name))
            await s.commit()
