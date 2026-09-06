"""Async database engine and session factory.

One async engine per process, chosen by `settings.database_url`
(sqlite+aiosqlite for dev/test, postgresql+asyncpg for production). `init_models`
creates tables directly — used for tests and first-run bootstrap; production uses
Alembic migrations instead.
"""

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .config import settings
from .models import Base

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.database_url, future=True)
        if settings.database_url.startswith("sqlite"):
            # SQLite ignores ON DELETE CASCADE unless foreign keys are enabled per
            # connection. Enable it so cascade behavior matches PostgreSQL.
            @event.listens_for(_engine.sync_engine, "connect")
            def _enable_fk(dbapi_conn, _record):  # pragma: no cover - trivial hook
                dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _sessionmaker


async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async context manager yielding a session that commits on success."""
    async with get_sessionmaker()() as session:
        yield session


async def init_models() -> None:
    """Create all tables (tests / first-run bootstrap; prod uses Alembic)."""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
