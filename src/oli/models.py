"""SQLAlchemy ORM models — the persistent schema.

Portable across SQLite (dev/test) and PostgreSQL (production). Embeddings are
stored as raw bytes (LargeBinary) for portability; on Postgres this becomes a
pgvector column in a later migration for indexed similarity search. IDs are hex
UUID strings for stability across databases.
"""

import time
import uuid

from sqlalchemy import Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> float:
    return time.time()


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200), default="New chat")
    created_at: Mapped[float] = mapped_column(Float, default=_now)
    updated_at: Mapped[float] = mapped_column(Float, default=_now)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | tool
    content: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes] = mapped_column(LargeBinary)  # float32 vector bytes
    source: Mapped[str] = mapped_column(String(16), default="auto")  # auto | explicit
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)


class ScheduledTask(Base):
    __tablename__ = "scheduled_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200))
    prompt: Mapped[str] = mapped_column(Text)
    schedule_kind: Mapped[str] = mapped_column(String(16))  # interval | daily
    interval_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    time_of_day: Mapped[str | None] = mapped_column(String(5), nullable=True)  # HH:MM
    enabled: Mapped[bool] = mapped_column(Integer, default=1)
    last_run: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_run: Mapped[float] = mapped_column(Float, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | error
    read: Mapped[bool] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)


class BrowserProfile(Base):
    """A named, persistent browser login the agent can reuse when browsing.

    The cookies themselves live on disk in ``PROFILES_DIR/<name>`` (a Chromium
    user-data dir written by a human login) — this row is only the metadata the
    UI needs. No credentials are stored here or anywhere in the database.
    """

    __tablename__ = "browser_profiles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # Stable, filesystem-safe key that names the on-disk profile dir.
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Human-friendly label shown in the UI.
    label: Mapped[str] = mapped_column(String(120), default="")
    # Where the login window first navigates (e.g. https://x.com/login).
    start_url: Mapped[str] = mapped_column(String(500), default="")
    last_login: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=_now, index=True)
