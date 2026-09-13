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


def _login_prompt(res: dict) -> str:
    """Message asking the user to set up / finish a login, when browse needs one."""
    label = res.get("label") or res.get("name")
    if label:
        return (
            f"I have a saved browser profile for **{label}** but it isn't logged in "
            "yet. Open the 🔐 Profiles panel and finish signing in to it once "
            "(your password never reaches me), then ask me again."
        )
    return (
        "That needs me to be signed in to the site, and I don't have a saved login "
        "for it. Open the 🔐 Profiles panel, add a profile for the site and sign in "
        "once (your password never reaches me), then ask me again."
    )


async def browse(goal: str, profile: str | None = None) -> str:
    """Drive a headless browser toward ``goal``.

    When ``profile`` names a saved browser profile, the run reuses that profile's
    authenticated cookies (from a prior human login) so it can act on logged-in
    pages. Credentials are never passed here — only the profile *name*.

    The engine is selected by ``settings.browse_engine``: ``"fara"`` routes to the
    native Fara-1.5 computer-use loop (local Ollama, no cloud fallback — see
    :mod:`oli.tools.fara_browse` and ADR 0016); anything else uses the legacy
    browser-use agent below.

    Profiles are resolved automatically: the user need not name a profile or paste a
    URL. When ``profile`` isn't given we match the goal to a saved profile by site
    and reuse its login URL; if the site needs a login we don't have, we ask the user
    to set one up; otherwise we browse normally (the web-search-style fallback).
    """
    start_url: str | None = None
    if not profile:
        with contextlib.suppress(Exception):
            res = await profiles.resolve_for_goal(goal)
            if res.get("action") == "use":
                profile = res.get("name")
                start_url = res.get("start_url")
            elif res.get("action") == "login":
                return _login_prompt(res)
            # "none" -> browse logged-out (normal browse / web-search fallback)

    if config.settings.browse_engine == "fara":
        from . import fara_browse

        return await fara_browse.browse_fara(goal, profile=profile, start_url=start_url)

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
            "For tasks on a site the user has an account on (their cart, orders, etc.), "
            "just say so in the goal (e.g. 'check my Amazon cart') — the right saved "
            "login is matched automatically; you do NOT need to ask the user for a "
            "profile name or a URL, and never ask for or pass passwords. If no login "
            "exists, browse returns a message telling the user how to set one up."
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
                        "Rarely needed: a saved profile is matched automatically from "
                        "the goal. Only set this to force a specific profile by name; "
                        "omit it otherwise (including for public browsing)."
                    ),
                },
            },
            "required": ["goal"],
        },
    },
}
