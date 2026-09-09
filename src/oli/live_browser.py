"""Live browser view — watch and drive a real browser from the Oli UI.

Streams a Chromium page to the frontend as JPEG frames over a WebSocket (via the
Chrome DevTools Protocol ``Page.startScreencast``) and dispatches the user's mouse
and keyboard back into the page (CDP ``Input.*``). It runs **fully headless**, so
it behaves identically on a laptop and on a headless VM — no virtual display,
no VNC stack.

This is the foundation of the "human-in-the-loop browser": you can open a live
session, drive it yourself (e.g. to log into a site), and — when bound to a
:mod:`oli.profiles` profile — the authenticated session persists to disk for the
agent to reuse. Single-user, so exactly one live session exists at a time.

Coordinates are sent from the UI as fractions in ``[0, 1]`` and scaled to the
fixed viewport here, which sidesteps device-pixel-ratio and canvas-scaling skew.
"""

import asyncio
import contextlib
from typing import Any

from . import profiles
from .logging_config import get_logger

log = get_logger(__name__)

# Fixed render size. Bigger = crisper but heavier to stream; this is a sensible
# middle ground for a single viewer on a small VM.
VIEWPORT = {"width": 1280, "height": 800}
SCREENCAST = {"format": "jpeg", "quality": 55, "maxWidth": 1280, "maxHeight": 800}

# Per-subscriber frame buffer. Small on purpose: if a viewer can't keep up we drop
# stale frames rather than let latency grow unbounded.
_QUEUE_MAX = 2

# Special keys we translate to CDP virtual key codes; everything else is typed as
# text via Input.insertText (robust across layouts, no keycode bookkeeping).
_SPECIAL_KEYS: dict[str, tuple[int, str]] = {
    "Enter": (13, "\r"),
    "Backspace": (8, ""),
    "Tab": (9, ""),
    "Escape": (27, ""),
    "ArrowLeft": (37, ""),
    "ArrowUp": (38, ""),
    "ArrowRight": (39, ""),
    "ArrowDown": (40, ""),
    "Delete": (46, ""),
    "Home": (36, ""),
    "End": (35, ""),
}


class LiveSession:
    """One live browser: a Chromium page, its screencast, and connected viewers."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._cdp: Any = None
        self._subscribers: set[asyncio.Queue] = set()
        self._last_frame: str | None = None  # newest frame, for instant late-join
        self._lock = asyncio.Lock()
        self.profile: str | None = None
        self.running = False

    # --- lifecycle -------------------------------------------------------

    async def start(self, profile: str | None = None, url: str | None = None) -> None:
        """Launch the browser and begin streaming. No-op if already running."""
        async with self._lock:
            if self.running:
                # Already up: just navigate if a URL was given.
                if url:
                    await self._goto(url)
                return

            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self.profile = profile

            if profile:
                # Persistent context => the login is saved to the profile's dir.
                user_data_dir = str(profiles.profile_dir(profile))
                profiles.profile_dir(profile).mkdir(parents=True, exist_ok=True)
                self._context = await self._pw.chromium.launch_persistent_context(
                    user_data_dir, headless=True, viewport=VIEWPORT
                )
                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else (await self._context.new_page())
                )
            else:
                self._browser = await self._pw.chromium.launch(headless=True)
                self._context = await self._browser.new_context(viewport=VIEWPORT)
                self._page = await self._context.new_page()

            self._cdp = await self._context.new_cdp_session(self._page)
            self._cdp.on("Page.screencastFrame", self._on_frame)
            await self._cdp.send("Page.startScreencast", SCREENCAST)
            self.running = True
            log.info("live_browser_started", profile=profile or "-")

        await self._goto(url or "about:blank")

    async def stop(self) -> str | None:
        """Stop streaming and close the browser. Returns the bound profile (if any)."""
        async with self._lock:
            if not self.running:
                return None
            profile = self.profile
            with contextlib.suppress(Exception):
                await self._cdp.send("Page.stopScreencast")
            # Persistent contexts flush cookies to disk on close.
            with contextlib.suppress(Exception):
                if self._context is not None:
                    await self._context.close()
            with contextlib.suppress(Exception):
                if self._browser is not None:
                    await self._browser.close()
            with contextlib.suppress(Exception):
                if self._pw is not None:
                    await self._pw.stop()
            # Wake any viewers so their loops exit.
            for q in list(self._subscribers):
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(None)
            self._subscribers.clear()
            self._reset()
            log.info("live_browser_stopped", profile=profile or "-")
            return profile

    def _reset(self) -> None:
        self._pw = self._browser = self._context = self._page = self._cdp = None
        self._last_frame = None
        self.profile = None
        self.running = False

    # --- streaming -------------------------------------------------------

    def _on_frame(self, event: dict) -> None:
        """CDP screencast callback: fan the frame out to viewers and ack it."""
        data = event.get("data")
        session_id = event.get("sessionId")
        if data is not None:
            self._last_frame = data
            for q in list(self._subscribers):
                if q.full():
                    with contextlib.suppress(asyncio.QueueEmpty):
                        q.get_nowait()  # drop the stalest frame
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(data)
        # Ack so Chromium keeps sending frames.
        if self._cdp is not None and session_id is not None:
            asyncio.create_task(
                self._cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            )

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
        # Prime with the newest frame so a late-joining viewer sees the current
        # page immediately, even if it's static and won't repaint on its own.
        if self._last_frame is not None:
            q.put_nowait(self._last_frame)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    # --- input + navigation ---------------------------------------------

    async def _goto(self, url: str) -> None:
        if self._page is None:
            return
        with contextlib.suppress(Exception):
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)

    async def navigate(self, url: str) -> None:
        if url and "://" not in url:
            url = "https://" + url
        await self._goto(url)

    async def dispatch(self, event: dict) -> None:
        """Translate a UI input event into a CDP Input.* call."""
        if not self.running or self._cdp is None:
            return
        kind = event.get("kind")
        try:
            if kind in ("mousemove", "mousedown", "mouseup"):
                await self._mouse(kind, event)
            elif kind == "wheel":
                await self._wheel(event)
            elif kind == "key":
                await self._key(event)
        except Exception as e:  # noqa: BLE001
            log.debug("live_input_failed", kind=kind, error=str(e))

    async def _mouse(self, kind: str, event: dict) -> None:
        x = float(event.get("xr", 0)) * VIEWPORT["width"]
        y = float(event.get("yr", 0)) * VIEWPORT["height"]
        cdp_type = {
            "mousemove": "mouseMoved",
            "mousedown": "mousePressed",
            "mouseup": "mouseReleased",
        }[kind]
        params: dict[str, Any] = {"type": cdp_type, "x": x, "y": y}
        if kind != "mousemove":
            params.update(button=event.get("button", "left"), clickCount=1)
        await self._cdp.send("Input.dispatchMouseEvent", params)

    async def _wheel(self, event: dict) -> None:
        x = float(event.get("xr", 0.5)) * VIEWPORT["width"]
        y = float(event.get("yr", 0.5)) * VIEWPORT["height"]
        await self._cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "deltaX": float(event.get("dx", 0)),
                "deltaY": float(event.get("dy", 0)),
            },
        )

    async def _key(self, event: dict) -> None:
        key = event.get("key", "")
        if key in _SPECIAL_KEYS:
            code, text = _SPECIAL_KEYS[key]
            for t in ("keyDown", "keyUp"):
                params = {"type": t, "windowsVirtualKeyCode": code, "key": key}
                if text and t == "keyDown":
                    params["text"] = text
                await self._cdp.send("Input.dispatchKeyEvent", params)
        elif len(key) == 1:
            # Printable character — insertText handles layout/shift for us.
            await self._cdp.send("Input.insertText", {"text": key})

    # --- status ----------------------------------------------------------

    def status(self) -> dict:
        url = ""
        if self._page is not None:
            with contextlib.suppress(Exception):
                url = self._page.url
        return {
            "running": self.running,
            "profile": self.profile,
            "url": url,
            "viewers": len(self._subscribers),
        }


# --- module singleton (one live session for the single-user app) ------------

_session = LiveSession()


def session() -> LiveSession:
    return _session
