"""Tests for persistent browser profiles.

These stay fully offline: no browser is ever launched. We exercise the storage
CRUD, the filesystem helpers, the manager's metadata flow, and the browse tool's
"not logged in" guard — the parts that don't need a real Chromium.
"""

import pytest

from oli import config, profiles
from oli.profiles import ProfileManager
from oli.tools import browse


@pytest.fixture(autouse=True)
def _isolate_profiles_dir(tmp_path, monkeypatch):
    """Point PROFILES_DIR at a throwaway dir so tests never touch real cookies."""
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path / "profiles")
    (tmp_path / "profiles").mkdir()


# --- storage CRUD --------------------------------------------------------


async def test_profile_storage_crud(storage):
    await storage.add_profile(name="twitter", label="Twitter", start_url="https://x.com")
    rows = await storage.list_profiles()
    assert [r["name"] for r in rows] == ["twitter"]

    got = await storage.get_profile_by_name("twitter")
    assert got["label"] == "Twitter" and got["last_login"] is None

    await storage.touch_profile_login("twitter", 123.0)
    assert (await storage.get_profile_by_name("twitter"))["last_login"] == 123.0

    await storage.delete_profile("twitter")
    assert await storage.get_profile_by_name("twitter") is None


# --- filesystem helpers --------------------------------------------------


def test_safe_name_normalises():
    assert profiles.safe_name("My Twitter!") == "my-twitter"
    assert profiles.safe_name("  ") == "profile"


def test_has_cookies_false_until_login():
    assert profiles.has_cookies("ghost") is False


def test_has_cookies_true_when_cookie_db_present():
    d = profiles.profile_dir("acct") / "Default"
    d.mkdir(parents=True)
    (d / "Cookies").write_bytes(b"")
    assert profiles.has_cookies("acct") is True


def test_build_profile_none_without_name():
    assert profiles.build_profile(None, headless=True) is None


# --- manager metadata flow -----------------------------------------------


async def test_manager_create_and_status(storage):
    mgr = ProfileManager(storage)
    await mgr.create("Gmail", "https://mail.google.com")
    rows = await mgr.list_with_status()
    assert rows[0]["name"] == "gmail"
    assert rows[0]["logged_in"] is False
    assert rows[0]["logging_in"] is False


async def test_manager_rejects_duplicate(storage):
    mgr = ProfileManager(storage)
    await mgr.create("Gmail", "")
    with pytest.raises(ValueError):
        await mgr.create("gmail", "")


async def test_manager_delete_removes_dir(storage):
    mgr = ProfileManager(storage)
    await mgr.create("Gmail", "")
    profiles.profile_dir("gmail").mkdir(parents=True, exist_ok=True)
    await mgr.delete("gmail")
    assert not profiles.profile_dir("gmail").exists()
    assert await storage.get_profile_by_name("gmail") is None


# --- browse guard --------------------------------------------------------


async def test_browse_refuses_unauthenticated_profile():
    """Requesting a not-logged-in profile returns a clear message, no browser."""
    result = await browse.browse("check my DMs", profile="ghost")
    assert "isn't logged in" in result


# --- API endpoints -------------------------------------------------------


async def test_profiles_api_crud(client):
    created = await client.post(
        "/api/profiles", json={"label": "Twitter", "start_url": "https://x.com"}
    )
    assert created.status_code == 200
    assert created.json()["name"] == "twitter"

    listed = (await client.get("/api/profiles")).json()
    assert any(p["name"] == "twitter" and p["logged_in"] is False for p in listed)

    deleted = await client.delete("/api/profiles/twitter")
    assert deleted.status_code == 200
    assert (await client.get("/api/profiles")).json() == []


async def test_profiles_api_rejects_duplicate(client):
    await client.post("/api/profiles", json={"label": "Gmail"})
    dup = await client.post("/api/profiles", json={"label": "gmail"})
    assert dup.status_code == 409


async def test_login_missing_profile_is_404(client):
    res = await client.post("/api/profiles/nope/login")
    assert res.status_code == 404
