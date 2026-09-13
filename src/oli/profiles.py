"""Persistent browser profiles — authenticated sessions the agent can reuse.

The security property this module exists to guarantee: **the LLM never sees
credentials.** A human logs into a site once in a real (headful) browser window;
Chromium persists the resulting cookies to a profile directory on disk; and every
later ``browse`` run reuses that directory headlessly. The model only ever refers
to a profile *by name* — there is no field anywhere that a password flows through.

Two halves live here:

* Pure filesystem/browser helpers (``profile_dir``, ``has_cookies``,
  ``build_profile``) — the browse tool resolves a profile's user-data dir through
  these; no database, easy to test.
* ``ProfileManager`` — store-aware orchestration of the interactive login flow
  (open window → human logs in → save), used by the API layer.

Interactive login needs a *visible* browser, so it works on a machine with a
display (your PC today). On a headless VM it raises a clear error; the noVNC
in-browser login flow is the planned follow-up (see docs/RUNBOOK.md).
"""

import asyncio
import contextlib
import re
import time
from pathlib import Path
from typing import Any

from . import config
from .logging_config import get_logger

log = get_logger(__name__)

# The login window stays open this long waiting for the human before we give up
# and reclaim the browser, so a forgotten session can't pin a browser forever.
LOGIN_TIMEOUT_SEC = 10 * 60


def safe_name(name: str) -> str:
    """Normalise a profile name into a stable, filesystem-safe directory key."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    return slug or "profile"


def profile_dir(name: str) -> Path:
    """On-disk Chromium user-data dir for a profile (the source of truth for cookies)."""
    return config.PROFILES_DIR / safe_name(name)


def has_cookies(name: str) -> bool:
    """Best-effort: has a human actually logged into this profile yet?

    Chromium writes a persistent ``Default/`` sub-profile once a context is used;
    the presence of a Cookies DB there means a session was established.
    """
    default = profile_dir(name) / "Default"
    return (default / "Cookies").exists() or (default / "Network" / "Cookies").exists()


def build_profile(name: str | None, *, headless: bool) -> Any | None:
    """Build a browser-use ``BrowserProfile`` bound to a named profile's dir.

    Returns None when no profile is requested (caller browses with a fresh browser)
    or when browser-use can't be imported.
    """
    if not name:
        return None
    try:
        from browser_use import BrowserProfile
    except Exception:  # noqa: BLE001
        return None
    d = profile_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    # user_data_dir makes the context persistent: cookies written during the human
    # login are read back here, so the headless agent starts already authenticated.
    return BrowserProfile(user_data_dir=str(d), headless=headless, keep_alive=False)


# Signals that a goal targets an *authenticated* site (needs a logged-in profile),
# so if we can't match one we should ask the user to log in rather than fail.
_ACCOUNT_TERMS = (
    "my cart",
    "my basket",
    "my order",
    "my account",
    "my wishlist",
    "my wish list",
    "wish list",
    "add to cart",
    "add to basket",
    "add it to",
    "checkout",
    "check out",
    "place an order",
    "sign in",
    "log in",
    "logged in",
    "my profile",
    "my subscriptions",
    "my watchlist",
)

# TLDs / prefixes stripped when reducing a start_url to a matchable site root, so
# "https://www.amazon.in/" -> "amazon" matches "check my amazon cart".
_STRIP_LABELS = {"www", "com", "in", "co", "org", "net", "io", "gov", "edu", "app"}


def _domain_root(url: str) -> str:
    """Reduce a URL to a matchable site root, e.g. https://www.amazon.in/ -> 'amazon'."""
    if not url:
        return ""
    from urllib.parse import urlparse

    host = urlparse(url if "://" in url else "https://" + url).netloc.lower().split(":")[0]
    parts = [p for p in host.split(".") if p and p not in _STRIP_LABELS]
    return parts[-1] if parts else host


async def resolve_for_goal(goal: str) -> dict:
    """Decide which saved profile (if any) a browse goal should use.

    The user shouldn't have to name the profile or paste the site URL — we match the
    goal against saved profiles by name / label / site-root and reuse the profile's
    own ``start_url`` (the link used at login) as the preferred entry point.

    Returns a dict with ``action``:
      * ``"use"``   — a logged-in profile matched; also gives ``name`` + ``start_url``.
      * ``"login"`` — the site needs a login we don't have (``name``/``label`` set when
                      a profile exists but isn't logged in; both None when none exists).
      * ``"none"``  — no profile needed; browse normally (the web-search fallback).
    """
    mgr = active()
    g = (goal or "").lower()
    rows = await mgr.list_with_status() if mgr is not None else []

    # 1. Match a saved profile by name, label, or its site root — as a *whole word*,
    #    so a short root like 'x' (x.com) doesn't match inside "netfli-x".
    for r in rows:
        root = _domain_root(r.get("start_url") or "")
        keys = {k for k in (r.get("name"), (r.get("label") or "").lower(), root) if k}
        if any(re.search(rf"\b{re.escape(k)}\b", g) for k in keys):
            # #1: matched a saved profile for this site — prefer it even for plain
            # reads (logged-in pages are cleaner/consistent), not just account actions.
            action = "use" if r.get("logged_in") else "login"
            return {
                "action": action,
                "name": r["name"],
                "label": r.get("label"),
                "start_url": r.get("start_url"),
            }

    # 2. No profile matched. If the goal clearly needs an authenticated site, ask the
    #    user to set one up rather than silently browsing logged-out.
    if any(t in g for t in _ACCOUNT_TERMS):
        return {"action": "login", "name": None, "label": None}

    # 3. Otherwise no profile is needed — browse normally.
    return {"action": "none"}


class ProfileManager:
    """Orchestrates the interactive login flow and records profile metadata.

    Bound to the same Storage the rest of the app uses. Live login browser sessions
    are held in-process, keyed by profile name, so a later "finish" request can
    close the right window and persist its cookies.
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        self._logins: dict[str, Any] = {}  # name -> live BrowserSession
        self._timers: dict[str, asyncio.TimerHandle] = {}

    async def list_with_status(self) -> list[dict]:
        """All profiles (DB metadata) annotated with live login/logged-in status."""
        rows = await self._store.list_profiles()
        for r in rows:
            r["logged_in"] = has_cookies(r["name"])
            r["logging_in"] = r["name"] in self._logins
        return rows

    async def create(self, label: str, start_url: str) -> dict:
        """Register a new profile (metadata only — no browser yet)."""
        label = label.strip() or "Profile"
        name = safe_name(label)
        if await self._store.get_profile_by_name(name):
            raise ValueError(f"A profile named '{name}' already exists.")
        await self._store.add_profile(name=name, label=label, start_url=start_url.strip())
        profile_dir(name).mkdir(parents=True, exist_ok=True)
        return {"name": name, "label": label, "start_url": start_url.strip()}

    async def start_login(self, name: str) -> None:
        """Open a visible browser at the profile's start URL for the human to log in.

        The window stays open (this returns immediately) until ``finish_login`` is
        called or the safety timeout fires.
        """
        profile = await self._store.get_profile_by_name(name)
        if not profile:
            raise ValueError(f"No profile named '{name}'.")
        if name in self._logins:
            return  # a login window is already open for this profile

        try:
            from browser_use import BrowserSession
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"browser-use unavailable: {e}") from e

        d = profile_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        session = BrowserSession(user_data_dir=str(d), headless=False, keep_alive=True)
        try:
            await session.start()
            start_url = profile.get("start_url") or "about:blank"
            await session.navigate_to(start_url)
        except Exception as e:  # noqa: BLE001
            # Most common cause on a server: no display to open a visible window.
            with_ctx = (
                f"Could not open a login window ({e}). Interactive login needs a "
                "machine with a display; on a headless VM use the noVNC flow (planned)."
            )
            with contextlib.suppress(Exception):
                await session.kill()
            raise RuntimeError(with_ctx) from e

        self._logins[name] = session
        loop = asyncio.get_running_loop()
        self._timers[name] = loop.call_later(
            LOGIN_TIMEOUT_SEC, lambda: asyncio.ensure_future(self._expire_login(name))
        )
        log.info("profile_login_started", profile=name)

    async def finish_login(self, name: str) -> dict:
        """Close the login window, persisting cookies to disk, and stamp last_login."""
        session = self._logins.pop(name, None)
        self._cancel_timer(name)
        if session is not None:
            try:
                await session.kill()
            except Exception as e:  # noqa: BLE001
                log.warning("profile_login_close_failed", profile=name, error=str(e))
        when = time.time()
        await self._store.touch_profile_login(name, when)
        log.info("profile_login_finished", profile=name, logged_in=has_cookies(name))
        return {"name": name, "logged_in": has_cookies(name), "last_login": when}

    def is_logging_in(self, name: str) -> bool:
        """True while an interactive login window is open for this profile.

        A live headful login holds the profile's user-data dir; a concurrent
        headless browse on the same dir would fail, so browse checks this first.
        """
        return name in self._logins

    async def delete(self, name: str) -> None:
        """Remove the profile: close any live login, delete the row and the on-disk cookies."""
        if name in self._logins:
            await self.finish_login(name)
        await self._store.delete_profile(name)
        _rmtree(profile_dir(name))
        log.info("profile_deleted", profile=name)

    async def shutdown(self) -> None:
        """Close any open login windows on server shutdown."""
        for name in list(self._logins):
            await self.finish_login(name)

    async def _expire_login(self, name: str) -> None:
        if name in self._logins:
            log.info("profile_login_expired", profile=name)
            await self.finish_login(name)

    def _cancel_timer(self, name: str) -> None:
        timer = self._timers.pop(name, None)
        if timer is not None:
            timer.cancel()


def _rmtree(path: Path) -> None:
    import shutil

    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


# --- module singleton (mirrors memory.set_active/active) --------------------

_active: ProfileManager | None = None


def set_active(manager: ProfileManager) -> None:
    global _active
    _active = manager


def active() -> ProfileManager | None:
    return _active
