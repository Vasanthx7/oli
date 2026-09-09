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

# browser-use drives headless Chromium; keep a step ceiling so a confused run can't loop forever.
MAX_STEPS = 20


def _build_llm():
    """Return a browser-use LLM adapter pointed at Groq.

    Prefer the native ChatGroq adapter (purpose-built for Groq); fall back to the
    OpenAI-compatible adapter aimed at Groq's endpoint if ChatGroq is unavailable.
    """
    # Native Groq adapter — talks to Groq directly, no base_url needed.
    try:
        from browser_use import ChatGroq

        return ChatGroq(model=config.BROWSER_MODEL, api_key=config.GROQ_API_KEY)
    except Exception:
        pass
    # Fallback: OpenAI-compatible adapter pointed at Groq's endpoint.
    from browser_use import ChatOpenAI

    return ChatOpenAI(
        model=config.BROWSER_MODEL,
        api_key=config.GROQ_API_KEY,
        base_url=config.GROQ_BASE_URL,
    )


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
