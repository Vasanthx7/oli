"""Tests for the live browser (CDP screencast + input).

The pure-logic paths (input translation, lifecycle guards, status) run always.
The end-to-end streaming test launches a real headless Chromium; if Playwright's
browser isn't installed (e.g. a bare CI runner), it skips rather than fails, so
the suite stays green everywhere and verifies for real where a browser exists.
"""

import asyncio

import pytest

from oli import live_browser
from oli.live_browser import LiveSession


async def _chromium_available() -> bool:
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True)
            await b.close()
        return True
    except Exception:  # noqa: BLE001
        return False


# --- logic that needs no browser -----------------------------------------


def test_singleton_session():
    assert live_browser.session() is live_browser.session()


async def test_dispatch_is_noop_when_not_running():
    # Should never raise even though no browser is attached.
    s = LiveSession()
    await s.dispatch({"kind": "mousedown", "xr": 0.5, "yr": 0.5})
    await s.dispatch({"kind": "key", "key": "a"})
    assert s.running is False


async def test_stop_when_idle_returns_none():
    s = LiveSession()
    assert await s.stop() is None


def test_subscribe_unsubscribe():
    s = LiveSession()
    q = s.subscribe()
    assert q in s._subscribers
    s.unsubscribe(q)
    assert q not in s._subscribers


def test_frame_fanout_and_drop_policy():
    """_on_frame pushes to subscribers and drops the stalest frame when full."""
    s = LiveSession()
    q = s.subscribe()
    # maxsize is 2; push 3 frames — oldest should be dropped, newest kept.
    for i in (b"a", b"b", b"c"):
        s._on_frame({"data": i.decode(), "sessionId": None})
    drained = [q.get_nowait() for _ in range(q.qsize())]
    assert drained == ["b", "c"]  # 'a' was dropped


def test_late_subscriber_gets_last_frame():
    """A viewer joining after the last paint must still see the current frame.

    Regression: a static page emits one frame; without priming, a WebSocket that
    connects afterwards would sit on a blank screen forever.
    """
    s = LiveSession()
    s._on_frame({"data": "current", "sessionId": None})  # a paint happens
    q = s.subscribe()  # viewer joins afterwards
    assert q.get_nowait() == "current"


# --- end-to-end (real headless Chromium) ---------------------------------


@pytest.mark.asyncio
async def test_live_stream_and_input_end_to_end():
    if not await _chromium_available():
        pytest.skip("Playwright Chromium not installed in this environment")

    s = LiveSession()
    await s.start(url="data:text/html,<h1>Oli live view</h1>")
    try:
        q = s.subscribe()
        frame = await asyncio.wait_for(q.get(), timeout=8)
        assert isinstance(frame, str) and len(frame) > 100  # base64 JPEG

        # Input dispatch must not raise against a real page.
        await s.dispatch({"kind": "mousemove", "xr": 0.5, "yr": 0.5})
        await s.dispatch({"kind": "mousedown", "xr": 0.5, "yr": 0.5, "button": "left"})
        await s.dispatch({"kind": "mouseup", "xr": 0.5, "yr": 0.5, "button": "left"})
        await s.dispatch({"kind": "key", "key": "h"})
        await s.dispatch({"kind": "key", "key": "Enter"})
        await s.dispatch({"kind": "wheel", "xr": 0.5, "yr": 0.5, "dx": 0, "dy": 120})

        assert s.status()["running"] is True
    finally:
        await s.stop()
    assert s.running is False
