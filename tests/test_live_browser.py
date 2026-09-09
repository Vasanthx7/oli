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


# --- agent-watch / take-control (no browser needed) ----------------------


class _FakeCDP:
    """Records CDP calls so tests can assert on input gating and teardown."""

    def __init__(self) -> None:
        self.sends: list[tuple] = []

    def on(self, *_a) -> None:
        pass

    async def send(self, method, params=None) -> None:
        self.sends.append((method, params))

    async def detach(self) -> None:
        self.sends.append(("detach", None))


class _FakeBrowser:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakePW:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class _FakeAgent:
    def __init__(self) -> None:
        self.paused = False
        self.resumed = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.resumed = True


def _fake_attached(agent=None) -> LiveSession:
    """A LiveSession wired into agent-watch mode without launching a browser."""
    s = LiveSession()
    s._cdp = _FakeCDP()
    s._browser = _FakeBrowser()
    s._pw = _FakePW()
    s._owns_browser = False
    s.agent_mode = True
    s.controlled = False
    s._agent = agent or _FakeAgent()
    s.running = True
    return s


async def test_control_is_noop_when_not_watching():
    s = LiveSession()  # idle, owned mode
    assert await s.take_control() is False
    assert await s.release_control() is False


async def test_input_is_gated_until_control_taken():
    """While watching, input is dropped; taking control lets it through."""
    s = _fake_attached()

    await s.dispatch({"kind": "mousedown", "xr": 0.5, "yr": 0.5, "button": "left"})
    assert s._cdp.sends == []  # ignored — user hasn't taken control

    assert await s.take_control() is True
    assert s.controlled is True
    await s.dispatch({"kind": "mousedown", "xr": 0.5, "yr": 0.5, "button": "left"})
    assert any(m == "Input.dispatchMouseEvent" for m, _ in s._cdp.sends)


async def test_take_and_release_pause_resume_the_agent():
    agent = _FakeAgent()
    s = _fake_attached(agent)

    await s.take_control()
    assert agent.paused is True and s.controlled is True

    await s.release_control()
    assert agent.resumed is True and s.controlled is False


async def test_attach_is_refused_while_a_session_is_running():
    s = LiveSession()
    s.running = True  # a session (owned or attached) is already up
    assert await s.attach_agent("ws://example/devtools/browser/x", _FakeAgent()) is False


async def test_stop_in_agent_mode_detaches_without_owning_browser():
    """A stop request while watching detaches our tap and resumes the agent."""
    agent = _FakeAgent()
    s = _fake_attached(agent)
    s.controlled = True  # user had taken control

    result = await s.stop()
    assert result is None
    assert s.running is False and s.agent_mode is False
    assert s._browser is None  # detached
    assert agent.resumed is True  # agent handed back before teardown


def test_status_exposes_agent_fields():
    st = LiveSession().status()
    assert st["agent_mode"] is False
    assert st["controlled"] is False


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
