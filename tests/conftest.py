"""Shared test fixtures.

Isolation: point the app at a throwaway SQLite file and inject a dummy API key
*before* any oli.* module is imported, so tests never touch real data or make
network calls. Each test gets freshly-created tables and a fresh async engine
(disposed after the test) to avoid cross-event-loop reuse.
"""

import os
import tempfile
import uuid
from pathlib import Path

# --- must run before importing any oli.* module ---
_TEST_DB = Path(tempfile.gettempdir()) / f"oli_test_{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB.as_posix()}"
os.environ["GROQ_API_KEY"] = "test-key-not-real"
os.environ["ENVIRONMENT"] = "test"

import httpx  # noqa: E402
import pytest_asyncio  # noqa: E402

from oli import db  # noqa: E402
from oli.models import Base  # noqa: E402
from oli.storage import Storage  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _fresh_db():
    """Recreate all tables per test, then dispose the engine (fresh per event loop)."""
    engine = db.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await db.dispose_engine()


@pytest_asyncio.fixture
async def storage() -> Storage:
    return Storage()


@pytest_asyncio.fixture
async def client():
    """Async HTTP client bound to the ASGI app, sharing the test's event loop."""
    from oli import main

    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
