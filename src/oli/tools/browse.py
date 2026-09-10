"""Autonomous browsing tool powered by browser-use.

Unlike web_fetch (which just reads one URL), this hands a *goal* to a self-driving
browser agent that can navigate, click, scroll, and fill forms across multiple pages,
then reports back what it found or did. It is the most capable and most expensive tool,
so the personality prompt tells the model to use it only when interaction is required.

browser-use's public API has shifted across releases, so the LLM adapter is imported
defensively: we try browser-use's own chat classes first, then fall back to langchain.
"""

import asyncio
import contextlib
from typing import Any

from .. import config, live_browser, profiles
from ..ratelimit import RateLimiter

# browser-use drives headless Chromium; keep a step ceiling so a confused run can't
# loop forever. Lower than the old 20 to cut the number of LLM calls per browse,
# which keeps us comfortably under Groq's per-minute request limit.
MAX_STEPS = 12

# Match the live view's canvas so watch/take-control coordinates line up 1:1 with
# what the agent's browser actually renders (see oli.live_browser.VIEWPORT).
VIEWPORT = {"width": 1280, "height": 800}

# Shared limiter: browse's per-step LLM calls are paced to Groq's request/min ceiling.
_limiter = RateLimiter(config.settings.browser_max_rpm)


def _build_llm():
    """Return a browser-use LLM adapter for the browser endpoint, rate-limited.

    Prefer the native ChatGroq adapter; fall back to the OpenAI-compatible adapter.
    Each adapter is subclassed so every `ainvoke` first passes through the shared
    rate limiter — so browser-use's per-step calls can't blow past Groq's limit.
    """
    key = config.settings.browser_api_key
    model = config.settings.browser_model
    base_url = config.settings.browser_base_url
    try:
        from browser_use import ChatGroq

        class _ThrottledChatGroq(ChatGroq):
            async def ainvoke(self, messages, output_format=None, **kwargs):
                await _limiter.acquire()
                return await super().ainvoke(messages, output_format, **kwargs)

        return _ThrottledChatGroq(model=model, api_key=key)
    except Exception:
        pass
    from browser_use import ChatOpenAI

    class _ThrottledChatOpenAI(ChatOpenAI):
        async def ainvoke(self, messages, output_format=None, **kwargs):
            await _limiter.acquire()
            return await super().ainvoke(messages, output_format, **kwargs)

    return _ThrottledChatOpenAI(model=model, api_key=key, base_url=base_url)


def _extract_result(history) -> str:
    """Pull a human-readable final answer out of whatever browser-use returned."""
    # AgentHistoryList exposes final_result() in modern versions.
    final = getattr(history, "final_result", None)
    if callable(final):
        try:
            r = final()
            if r:
                return str(r)
        except Exception:
            pass
    return str(history)


async def browse(goal: str, profile: str | None = None) -> str:
    """Drive a headless browser toward ``goal``.

    When ``profile`` names a saved browser profile, the run reuses that profile's
    authenticated cookies (from a prior human login) so it can act on logged-in
    pages. Credentials are never passed here — only the profile *name*.
    """
    try:
        from browser_use import Agent, BrowserSession
    except Exception as e:  # noqa: BLE001
        return f"browse unavailable: could not import browser-use ({e})"

    try:
        llm = _build_llm()
    except Exception as e:  # noqa: BLE001
        return f"browse unavailable: could not build LLM adapter ({e})"

    # Resolve a persistent, authenticated profile if one was requested.
    user_data_dir: str | None = None
    if profile:
        # A login window open for this profile holds its user-data dir; launching a
        # second (headless) browser on the same dir would fail. Refuse clearly.
        mgr = profiles.active()
        if mgr is not None and mgr.is_logging_in(profile):
            return (
                f"browse: a login window for '{profile}' is open. Finish or close "
                "it in the 🔐 Profiles panel, then retry."
            )
        if not profiles.has_cookies(profile):
            return (
                f"browse: profile '{profile}' isn't logged in yet. Open the "
                "🔐 Profiles panel and sign in once, then retry."
            )
        pdir = profiles.profile_dir(profile)
        pdir.mkdir(parents=True, exist_ok=True)
        user_data_dir = str(pdir)

    # We own the BrowserSession (rather than letting Agent create its own) so the
    # live view can tap its screencast and the user can watch — and, via
    # attach_agent, take control mid-run. Fixed viewport keeps take-control clicks
    # aligned with the streamed frame.
    session = await _start_session(BrowserSession, user_data_dir)
    if session is None:
        return "browse failed: could not start the browser."

    live = live_browser.session()
    attached = False
    try:
        # use_vision=False: the default browser model (Groq gpt-oss-120b) is
        # text-only and rejects browser-use's multimodal message format
        # ("messages[].content must be a string"). Text-only sends the DOM/AX
        # tree as a string, which the model accepts. Set OLI vision only with a
        # vision-capable browser model.
        agent: Any = Agent(task=goal, llm=llm, browser_session=session, use_vision=False)
        # Best-effort tap: if a user is already driving their own live browser,
        # attach_agent returns False and the run simply proceeds unwatched.
        cdp_url = getattr(session, "cdp_url", None)
        if cdp_url:
            with contextlib.suppress(Exception):
                attached = await live.attach_agent(cdp_url, agent)
        # Some versions accept max_steps on run(); tolerate signature differences.
        try:
            history = await agent.run(max_steps=MAX_STEPS)
        except TypeError:
            history = await agent.run()
        return _extract_result(history)
    except Exception as e:  # noqa: BLE001
        return f"browse failed: {e}"
    finally:
        if attached:
            with contextlib.suppress(Exception):
                await live.detach()
        with contextlib.suppress(Exception):
            await session.kill()


async def _start_session(browser_session_cls: Any, user_data_dir: str | None) -> Any:
    """Start a headless BrowserSession sized to the live viewport.

    ``viewport`` is dropped on a retry because browser-use's accepted kwargs (and
    their validation) have shifted across releases; an unsized session still streams
    and runs fine. Any failure on the sized attempt therefore falls back to the
    minimal set rather than aborting the browse.
    """
    base: dict[str, Any] = {"headless": True, "keep_alive": False}
    if user_data_dir:
        base["user_data_dir"] = user_data_dir
    candidates = ({**base, "viewport": VIEWPORT}, base)
    for i, kwargs in enumerate(candidates):
        is_last = i == len(candidates) - 1
        try:
            session = browser_session_cls(**kwargs)
            await session.start()
            return session
        except Exception:  # noqa: BLE001
            if is_last:
                return None
            # Sized attempt failed (unknown/invalid kwarg) — retry minimally.
    return None


# Guard concurrent browse runs — one headless Chromium at a time keeps memory sane on a small VM.
_browse_lock = asyncio.Lock()


async def browse_serialized(goal: str, profile: str | None = None) -> str:
    async with _browse_lock:
        return await browse(goal, profile=profile)


SCHEMA = {
    "type": "function",
    "function": {
        "name": "browse",
        "description": (
            "Delegate a goal to an autonomous browser that can navigate, click, scroll, "
            "and fill forms across multiple pages. Use ONLY when reading a page isn't "
            "enough — i.e. when the task needs interaction, multi-step navigation, or a "
            "dynamic/JavaScript-heavy site. Describe the goal in plain language "
            "(e.g. 'Go to news.ycombinator.com and list the top 3 story titles'). "
            "To act on a site the user is logged into, pass 'profile' with the name of "
            "one of their saved browser profiles; never ask for or pass passwords."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "The browsing goal, in plain language.",
                },
                "profile": {
                    "type": "string",
                    "description": (
                        "Optional name of a saved, logged-in browser profile to reuse "
                        "(e.g. 'twitter'). Omit for public browsing."
                    ),
                },
            },
            "required": ["goal"],
        },
    },
}
