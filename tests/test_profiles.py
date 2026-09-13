"""Offline tests for profile auto-resolution (goal -> saved profile + login URL)."""

from __future__ import annotations

import asyncio

from oli import profiles


def test_domain_root_reduces_url_to_site():
    assert profiles._domain_root("https://www.amazon.in/") == "amazon"
    assert profiles._domain_root("https://x.com/") == "x"
    assert profiles._domain_root("flipkart.com") == "flipkart"
    assert profiles._domain_root("https://booking.com/") == "booking"
    assert profiles._domain_root("") == ""


class _FakeMgr:
    def __init__(self, rows):
        self._rows = rows

    async def list_with_status(self):
        return self._rows


def _resolve(goal, rows):
    profiles.set_active(_FakeMgr(rows))
    try:
        return asyncio.run(profiles.resolve_for_goal(goal))
    finally:
        profiles.set_active(None)  # type: ignore[arg-type]


_ROWS = [
    {"name": "amazon", "label": "Amazon", "start_url": "https://www.amazon.in/", "logged_in": True},
    {"name": "twitter", "label": "Twitter", "start_url": "https://x.com/", "logged_in": False},
]


def test_matches_logged_in_profile_and_returns_its_url():
    r = _resolve("add a usb-c cable to my amazon cart", _ROWS)
    assert r["action"] == "use"
    assert r["name"] == "amazon"
    assert r["start_url"] == "https://www.amazon.in/"


def test_matched_but_not_logged_in_asks_to_login():
    r = _resolve("post a tweet on twitter", _ROWS)
    assert r["action"] == "login"
    assert r["name"] == "twitter"


def test_short_domain_root_does_not_false_match_substring():
    # 'x' (x.com) must not match inside "netflix".
    r = _resolve("log in to my netflix and show my watchlist", _ROWS)
    assert r["action"] == "login"
    assert r["name"] is None  # no netflix profile; prompt to create one


def test_no_profile_needed_browses_normally():
    r = _resolve("go to wikipedia and tell me about Alan Turing", _ROWS)
    assert r["action"] == "none"


def test_no_manager_is_safe():
    profiles.set_active(None)  # type: ignore[arg-type]
    r = asyncio.run(profiles.resolve_for_goal("check my amazon cart"))
    # No manager -> can't match a profile; account term still routes to a login prompt.
    assert r["action"] in ("login", "none")
