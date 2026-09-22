"""Autonomous browsing tool — computer-use via the local Fara-1.5 engine.

Unlike web_fetch (which just reads one URL), this hands a *goal* to a self-driving
browser agent that can navigate, click, scroll, and fill forms across multiple pages,
then reports back what it found or did. It is the most capable and most expensive tool,
so the personality prompt tells the model to use it only when interaction is required.

Computer-use is **vision-only and local by design** (see ADR 0016/0017): it runs on a
local Ollama serving Fara-1.5 — the open-source model built for this — in both dev and
production, and it is the *only* workload that uses a local model. There is no cloud
fallback: if the Fara host is unreachable, :mod:`oli.tools.fara_browse` returns a clear
"unavailable" message (optionally pointing at a recorded walkthrough) rather than
silently burning the cloud chat providers' quota.

This module owns the pre-flight concerns that sit *around* the engine — the irreversible
-purchase guardrail, automatic profile resolution, and inline login — then delegates the
actual driving to :func:`oli.tools.fara_browse.browse_fara`.
"""

import asyncio
import contextlib

from .. import live_browser, profiles
from ..logging_config import get_logger

log = get_logger(__name__)


# Irreversible commercial actions we must NOT take autonomously (#4). "buy a cable"
# colloquially means "shop for / add to cart", so only the strong final-step phrases
# gate here; the browse task also carries a safety instruction (fara_browse) telling
# the model to stop before any place-order/pay click.
_PURCHASE_TERMS = (
    "place order",
    "place the order",
    "place an order",
    "complete purchase",
    "complete the purchase",
    "confirm purchase",
    "confirm the order",
    "pay now",
    "make payment",
    "make the payment",
    "pay for",
    "checkout and pay",
    "buy now",
    "proceed to pay",
)


def _needs_purchase_confirm(goal: str) -> bool:
    g = goal.lower()
    return any(t in g for t in _PURCHASE_TERMS)


_PURCHASE_REFUSAL = (
    "I won't place an order or complete a payment automatically — that step is "
    "irreversible and spends real money. I can add the item(s) to your cart and take "
    "you to checkout, then you review and pay yourself. Want me to do that?"
)


async def _offer_login(res: dict) -> str:
    """Handle a login-needed result (#3): open the site in the live browser view for an
    inline sign-in when we can, else tell the user how to set one up."""
    name = res.get("name")
    label = res.get("label") or name
    url = res.get("start_url")
    if name and url:
        live = live_browser.session()
        try:
            await live.start(profile=name, url=url)
            return (
                f"You're not signed in to **{label}** yet. I've opened it in the browser "
                "panel — please sign in there, then close the panel and ask me again, and "
                "I'll continue with your account. (Your password never reaches me.)"
            )
        except Exception as e:  # noqa: BLE001
            log.warning("inline_login_failed", profile=name, error=str(e))
    if label:
        return profiles.relogin_message(label)
    return (
        "That needs me to be signed in to the site, and I don't have a saved login for "
        "it. Open the Profiles panel, add a profile for the site and sign in once "
        "(your password never reaches me), then ask me again."
    )


async def browse(goal: str, profile: str | None = None) -> str:
    """Drive a headless browser toward ``goal`` with the local Fara-1.5 engine.

    When ``profile`` names a saved browser profile, the run reuses that profile's
    authenticated cookies (from a prior human login) so it can act on logged-in
    pages. Credentials are never passed here — only the profile *name*.

    Profiles are resolved automatically: the user need not name a profile or paste a
    URL. When ``profile`` isn't given we match the goal to a saved profile by site
    and reuse its login URL; if the site needs a login we don't have, we ask the user
    to set one up; otherwise we browse logged-out (the web-search-style fallback).
    """
    # #4: never auto-complete an irreversible purchase — offer cart + checkout instead.
    if _needs_purchase_confirm(goal):
        return _PURCHASE_REFUSAL

    start_url: str | None = None
    if not profile:
        res: dict = {}
        with contextlib.suppress(Exception):
            res = await profiles.resolve_for_goal(goal)
        if res.get("action") == "use":
            profile = res.get("name")
            start_url = res.get("start_url")
        elif res.get("action") == "login":
            return await _offer_login(res)
        # "none" -> browse logged-out (normal browse / web-search fallback)

    # An open inline login holds this profile's user-data dir; a headless browse on the
    # same dir would fail, so ask the user to finish signing in first.
    if profile:
        live = live_browser.session()
        if (
            getattr(live, "running", False)
            and not getattr(live, "agent_mode", False)
            and getattr(live, "profile", None) == profile
        ):
            return (
                f"Finish signing in to '{profile}' and close the browser panel, then ask me again."
            )

    from . import fara_browse

    return await fara_browse.browse_fara(goal, profile=profile, start_url=start_url)


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
