"""Autonomous browsing tool powered by browser-use.

Unlike web_fetch (which just reads one URL), this hands a *goal* to a self-driving
browser agent that can navigate, click, scroll, and fill forms across multiple pages,
then reports back what it found or did. It is the most capable and most expensive tool,
so the personality prompt tells the model to use it only when interaction is required.

browser-use's public API has shifted across releases, so the LLM adapter is imported
defensively: we try browser-use's own chat classes first, then fall back to langchain.
"""

import asyncio
from typing import Any

from .. import config, profiles
from ..ratelimit import RateLimiter

# browser-use drives headless Chromium; keep a step ceiling so a confused run can't
# loop forever. Lower than the old 20 to cut the number of LLM calls per browse,
# which keeps us comfortably under Groq's per-minute request limit.
MAX_STEPS = 12

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
        from browser_use import Agent
    except Exception as e:  # noqa: BLE001
        return f"browse unavailable: could not import browser-use ({e})"

    try:
        llm = _build_llm()
    except Exception as e:  # noqa: BLE001
        return f"browse unavailable: could not build LLM adapter ({e})"

    # Resolve a persistent, authenticated profile if one was requested.
    browser_profile = None
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
        browser_profile = profiles.build_profile(profile, headless=True)

    try:
        kwargs: dict[str, Any] = {"task": goal, "llm": llm}
        if browser_profile is not None:
            kwargs["browser_profile"] = browser_profile
        agent: Any = Agent(**kwargs)
        # Some versions accept max_steps on run(); tolerate signature differences.
        try:
            history = await agent.run(max_steps=MAX_STEPS)
        except TypeError:
            history = await agent.run()
        return _extract_result(history)
    except Exception as e:  # noqa: BLE001
        return f"browse failed: {e}"


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
