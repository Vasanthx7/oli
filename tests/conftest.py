"""Shared test fixtures.

Isolation strategy: point the app at a throwaway SQLite file and inject a dummy
API key *before* any `oli` module is imported, so tests never touch the real DB
or make real network calls. os.environ takes precedence over the .env file in
pydantic-settings, so this cleanly overrides local dev config.
"""

import os
import tempfile
import uuid
from pathlib import Path

# --- must run before importing any oli.* module ---
_TEST_DB = Path(tempfile.gettempdir()) / f"oli_test_{uuid.uuid4().hex}.db"
os.environ["DB_PATH"] = str(_TEST_DB)
os.environ["GROQ_API_KEY"] = "test-key-not-real"
os.environ["ENVIRONMENT"] = "test"

import pytest  # noqa: E402

from oli.storage import Storage  # noqa: E402


@pytest.fixture
def storage(tmp_path) -> Storage:
    """A fresh, isolated Storage backed by a per-test temp DB file."""
    db = tmp_path / "unit.db"
    s = Storage(db)
    yield s
    s.close()


@pytest.fixture
def client():
    """FastAPI TestClient with a clean database per test."""
    from fastapi.testclient import TestClient

    from oli import main

    # Wipe all tables so each test starts clean (shared app-level store).
    with main.store._lock:
        for table in ("notifications", "scheduled_tasks", "memories", "messages", "conversations"):
            main.store._conn.execute(f"DELETE FROM {table}")
        main.store._conn.commit()

    with TestClient(main.app) as c:
        yield c
