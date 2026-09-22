"""Native Fara-1.5 computer-use browse engine.

The `browse` tool's high-quality path: a vision-only observe-think-act loop that
drives a real Chromium and is served by a **local Ollama running Fara1.5-4B**.
Fara sees the page through screenshots (no DOM), reasons in text, and emits
grounded actions (click at pixel coordinates, type, scroll, visit URL, …). This
is the loop Microsoft ships with the model (MagenticLite); we reproduce it here
using oli's own Playwright + `openai` client so we don't have to take the `fara`
package as a dependency (it pins `playwright==1.51`, which clashes with the one
browser-use needs). The system prompt is vendored **verbatim** from the model
(``fara_system_prompt.txt``) — the card is explicit that it must be used as-is.

Deliberate design choices (see ADR 0016):

* **No cloud fallback.** If the Fara host is unreachable we return a clear
  "unavailable" message (optionally pointing at a recorded walkthrough) rather
  than silently spending Groq's rate-limited quota. Fara is the engine, period.
* **Watch + take-control.** The page is streamed to the live view; the user can
  pause the agent mid-run, drive it by hand, and resume — via a simple asyncio
  gate the loop checks at each step boundary.
* **1440×900, temperature 0, most-recent-3 screenshots** — the model's trained
  operating point, for the most reliable coordinate grounding.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from openai import AsyncOpenAI

from .. import config, live_browser, profiles
from ..logging_config import get_logger
from ..net_guard import UnsafeURLError, validate_url

log = get_logger(__name__)

# Fara's trained operating point. 1440×900 gives the most reliable grounding;
# coordinates come back in a normalized 0–DISPLAY_SIZE space and scale to pixels.
VIEWPORT = {"width": 1440, "height": 900}
DISPLAY_SIZE = 1000
# Local Fara has no per-minute rate limit (the browser-use path's MAX_STEPS=12 was a
# Groq-rate compromise), and real interactive flows — search → open result → scroll →
# add to cart → confirm — need the room. This budget is shared across a whole resumable
# session (pauses for user handover consume from it — see ADR 0018), so it's set high
# enough that a clarify-then-continue flow can still finish; bounded so a confused run ends.
MAX_STEPS = 50
MAX_IMAGES = 3  # keep only the most recent N screenshots in the prompt
USER_MESSAGE = "Here is the next screenshot. Think about what to do next."

# Fara-4B's weak spots are form-fill and search-box interaction (it stalls, e.g.
# scrolling in place looking for a submit button — see ADR 0016 / BENCHMARK.md).
# We steer those *without* touching the verbatim system prompt: a short operational
# hint appended to the user task, plus a runtime anti-stall nudge (below).
_TASK_HINT = (
    "\n\nTips: To fill a form, click a field, type its value, move to the next field, "
    "and once all fields are filled click the form's submit/post button. To use a "
    "search box, click it, type the query, then press Enter. If the screenshot looks "
    "unchanged after an action, do NOT repeat it — try a different target or action."
)
# Injected when the model repeats the same action type with no progress. Action-aware:
# clicking a text field repeatedly means it's already focused (type instead); scrolling
# repeatedly means the target is likely already on screen (click it, stop scrolling).
_STUCK_HINTS = {
    "left_click": (
        "You have clicked the same spot several times with no change. If it is a text "
        "field it is ALREADY focused — TYPE the text now instead of clicking again. If "
        "nothing is happening, the element may be elsewhere; pick a different target."
    ),
    "scroll": (
        "You have scrolled repeatedly with no progress. Stop scrolling — the element you "
        "need (e.g. the submit/post button or a link) is likely already visible; click "
        "it directly."
    ),
    "type": (
        "You have typed repeatedly. Do not type again — move on: submit the form (click "
        "its submit/post button) or press Enter, or click the next field you need."
    ),
}
_STUCK_HINT = (
    "You have repeated the same action several times with no visible change. Re-read the "
    "screenshot and pick a DIFFERENT action or target."
)
# Hard safety rail (#4): the model must never complete an irreversible purchase.
_SAFETY_SUFFIX = (
    "\n\nSafety: NEVER place an order or complete a payment. Do not click 'Place order', "
    "'Buy now', 'Pay', 'Proceed to pay', or otherwise confirm a purchase. If the task "
    "would require that, stop just before it and report what remains for the user to do."
)
# Anti-hallucination: don't claim a state-changing action worked without checking the
# real state. A product tile flipping to "in cart" is not proof — especially when signed
# out, where the add doesn't persist. Force a verification pass before reporting success.
_VERIFY_SUFFIX = (
    "\n\nVerify before finishing: if the task was to add something to a cart/bag/list, OPEN "
    "the cart/bag page and confirm the item AND the cart count/subtotal are actually there "
    "before you report success. A product tile showing 'in cart' is NOT proof. If the cart "
    "does not show the item (for example because you are signed out), say it could not be "
    "added and why — do NOT claim it was added."
)
# Unavailable / can't-add handling: don't dead-end at an out-of-stock listing, and never
# silently substitute a different product. Gather the available options, then ASK.
_ALTERNATIVES_SUFFIX = (
    "\n\nIf the exact product to add is unavailable or has no 'Add to cart' button "
    "('Currently unavailable', out of stock, or only 'Add to Wish List'), do NOT stop at "
    "that page and do NOT add anything else on your own. First look for available "
    "equivalents: 'See all buying options'/other sellers, other variants (color, storage), "
    "or search the same model again. Then ask the user which available option to add, "
    "listing each option's name, price, and seller. Add an item only after they choose. If "
    "nothing equivalent is available, say the item is currently unavailable and stop."
)

_SYSTEM_PROMPT = (Path(__file__).parent / "fara_system_prompt.txt").read_text(encoding="utf-8")

# Auto-route: interaction-heavy or dense/cluttered-page goals go to the "careful"
# (9B) tier — 4B grounds well on clean read/nav pages but lands near, not on, small
# controls on busy pages (benchmark + Amazon debugging). Clean read/nav/extract
# stays on the fast (4B) tier. Keyword heuristic over the goal text.
_INTERACTION_TERMS = (
    "add to cart",
    "add to basket",
    "add to list",
    "wishlist",
    "checkout",
    "check out",
    "buy ",
    "purchase",
    "place an order",
    "order ",
    "book ",
    "booking",
    "reserve",
    "fill in",
    "fill out",
    "fill the",
    "form",
    "submit",
    "sign in",
    "log in",
    "login",
    "apply",
    "reply",
    "upload",
    "subscribe",
    "add it to",
)
_DENSE_DOMAINS = (
    "amazon.",
    "flipkart.",
    "myntra.",
    "ebay.",
    "walmart.",
    "aliexpress.",
    "target.com",
    "bestbuy.",
    "booking.com",
    "expedia.",
    "makemytrip.",
    "swiggy.",
    "zomato.",
    "duckduckgo.",
)


def _route_model(goal: str) -> tuple[str, str]:
    """Pick the model tier for ``goal``. Returns (model_tag, reason)."""
    if not config.settings.fara_autoroute:
        return config.settings.fara_model, "autoroute-off"
    g = goal.lower()
    hit = next((t for t in _INTERACTION_TERMS if t in g), None) or next(
        (d for d in _DENSE_DOMAINS if d in g), None
    )
    if hit:
        return config.settings.fara_model_heavy, f"heavy ({hit.strip()})"
    return config.settings.fara_model, "fast (read/nav)"


# Fara key names → Playwright key names (best-effort; unknowns pass through).
_KEY_MAP = {
    "return": "Enter",
    "enter": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "tab": "Tab",
    "space": "Space",
    "backspace": "Backspace",
    "delete": "Delete",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "ctrl": "Control",
    "control": "Control",
    "cmd": "Meta",
    "command": "Meta",
    "meta": "Meta",
    "alt": "Alt",
    "shift": "Shift",
    "pageup": "PageUp",
    "pagedown": "PageDown",
    "home": "Home",
    "end": "End",
}


class _Unavailable(Exception):
    """Raised when the Fara model host can't be reached — surfaced as a message."""


class FaraBrowseAgent:
    """Holds the pause/resume gate the live view toggles for take-control."""

    def __init__(self) -> None:
        # set = running, cleared = paused (user has control). Starts running.
        self._resumed = asyncio.Event()
        self._resumed.set()

    def pause(self) -> None:
        self._resumed.clear()

    def resume(self) -> None:
        self._resumed.set()

    async def wait_if_paused(self) -> None:
        await self._resumed.wait()


def _b64_data_uri(png: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(png).decode()}"


def _to_px(coord: list[float] | None) -> tuple[int, int]:
    """Fara's 0–DISPLAY_SIZE coordinate → viewport pixel (defaults to origin)."""
    if not coord:
        return 0, 0
    x, y = coord[0], coord[1]
    return int(x * VIEWPORT["width"] / DISPLAY_SIZE), int(y * VIEWPORT["height"] / DISPLAY_SIZE)


def _parse(message: str) -> tuple[str, dict[str, Any]]:
    """Split a Fara response into (thoughts, action_args).

    Format: free-text thoughts, then ``<tool_call>\\n{json}\\n</tool_call>`` where
    the JSON is ``{"name": "computer_use", "arguments": {"action": ..., ...}}``.
    """
    try:
        head, _, tail = message.partition("<tool_call>")
        thoughts = head.strip()
        action_text = tail.split("</tool_call>")[0].strip()
        obj = json.loads(action_text)
        args = obj.get("arguments", obj)
        args["thoughts"] = thoughts
        return thoughts, args
    except Exception:
        # No parseable tool call — treat the whole message as a terminate answer so
        # the run ends with something useful instead of raising.
        return message.strip(), {"action": "terminate", "answer": message.strip()}


def _trim_images(messages: list[dict], keep: int) -> None:
    """Strip image parts from all but the most recent ``keep`` image-bearing msgs."""
    idxs = [
        i
        for i, m in enumerate(messages)
        if isinstance(m.get("content"), list)
        and any(p.get("type") == "image_url" for p in m["content"])
    ]
    for i in idxs[:-keep] if keep > 0 else idxs:
        m = messages[i]
        kept = [p for p in m["content"] if p.get("type") != "image_url"]
        m["content"] = kept or [{"type": "text", "text": "(screenshot omitted)"}]


async def _screenshot(page: Any) -> bytes:
    """Capture a PNG, retrying through transient mid-navigation failures.

    Chromium's captureScreenshot can fail while a navigation is committing; a short
    wait + retry rides over it rather than aborting the whole run.
    """
    last: Exception | None = None
    for _attempt in range(3):
        try:
            return await page.screenshot(type="png")
        except Exception as e:  # noqa: BLE001
            last = e
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            await asyncio.sleep(0.6)
    raise last if last else RuntimeError("screenshot failed")


async def _dispatch(page: Any, action: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Execute one action. Returns (is_terminal, observation/answer)."""
    coord = args.get("coordinate")

    if action == "left_click":
        x, y = _to_px(coord)
        await page.mouse.click(x, y)
        return False, f"clicked ({x},{y})"
    if action in ("double_click",):
        x, y = _to_px(coord)
        await page.mouse.dblclick(x, y)
        return False, f"double-clicked ({x},{y})"
    if action == "right_click":
        x, y = _to_px(coord)
        await page.mouse.click(x, y, button="right")
        return False, f"right-clicked ({x},{y})"
    if action == "triple_click":
        x, y = _to_px(coord)
        await page.mouse.click(x, y, click_count=3)
        return False, f"triple-clicked ({x},{y})"
    if action == "mouse_move":
        x, y = _to_px(coord)
        await page.mouse.move(x, y)
        return False, f"moved to ({x},{y})"
    if action == "left_click_drag":
        x, y = _to_px(coord)
        await page.mouse.down()
        await page.mouse.move(x, y)
        await page.mouse.up()
        return False, f"dragged to ({x},{y})"
    if action == "type":
        text = str(args.get("text", args.get("text_value", "")))
        await page.keyboard.type(text)
        return False, f"typed {text!r}"
    if action == "key":
        keys = args.get("keys", [])
        if isinstance(keys, str):
            keys = [keys]
        mapped = [_KEY_MAP.get(str(k).lower(), str(k)) for k in keys]
        if len(mapped) > 1:  # a chord, e.g. Control+C
            await page.keyboard.press("+".join(mapped))
        elif mapped:
            await page.keyboard.press(mapped[0])
        return False, f"pressed {mapped}"
    if action == "scroll":
        pixels = int(args.get("pixels", 0)) * VIEWPORT["height"] // DISPLAY_SIZE
        await page.mouse.wheel(0, -pixels)  # Fara: positive pixels = scroll up
        return False, "scrolled"
    if action == "hscroll":
        pixels = int(args.get("pixels", 0)) * VIEWPORT["width"] // DISPLAY_SIZE
        await page.mouse.wheel(pixels, 0)
        return False, "scrolled horizontally"
    if action == "visit_url":
        url = str(args.get("url", ""))
        # Guard the agent-chosen URL: http(s) to a public host only — never file://
        # or an internal/metadata address. Feed refusals back so the loop continues.
        try:
            target = await asyncio.to_thread(validate_url, url)
        except UnsafeURLError as e:
            return False, f"refused to visit unsafe url {url!r}: {e}"
        with contextlib.suppress(Exception):
            await page.goto(target, wait_until="domcontentloaded", timeout=30000)
        return False, f"navigated to {url}"
    if action == "web_search":
        q = str(args.get("query", ""))
        with contextlib.suppress(Exception):
            await page.goto(
                f"https://www.bing.com/search?q={quote_plus(q)}&FORM=QBLH",
                wait_until="domcontentloaded",
                timeout=30000,
            )
        return False, f"searched {q!r}"
    if action == "history_back":
        with contextlib.suppress(Exception):
            await page.go_back(wait_until="domcontentloaded", timeout=30000)
        return False, "went back"
    if action == "wait":
        await asyncio.sleep(min(float(args.get("time", args.get("duration", 3.0))), 10.0))
        return False, "waited"
    if action == "pause_and_memorize_fact":
        return False, f"noted: {args.get('fact', '')}"
    if action == "read_page_answer_question":
        # Terminal-ish read handled by the caller (needs the client); signal via a
        # sentinel so the loop can run the follow-up QA call.
        return False, "__READ__:" + str(args.get("question", ""))
    if action == "ask_user_question":
        # Terminal-for-this-step, but a *pause* not a finish: the resumable path (see
        # _drive/_pump) surfaces the question to the user and resumes on their reply.
        # Sentinel lets _drive tell an ask apart from a real terminate.
        return True, "__ASK__:" + str(args.get("question", ""))
    if action == "terminate":
        return True, str(args.get("answer", args.get("thoughts", "")))

    return False, f"(unsupported action: {action})"


async def _read_page_answer(client: AsyncOpenAI, page: Any, question: str, model: str) -> str:
    """Fara's read_page_answer_question: pull page text and answer with the model."""
    text = ""
    with contextlib.suppress(Exception):
        text = await page.evaluate("document.body.innerText")
    text = (text or "")[:8000]
    with contextlib.suppress(Exception):
        r = await client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": f"Answer the question from this page text.\n\n"
                    f"Question: {question}\n\nPage text:\n{text}",
                }
            ],
        )
        return (r.choices[0].message.content or "").strip()
    return "(could not read page)"


async def _signed_in(st: _RunState) -> bool:
    """Best-effort check that a profile run landed AUTHENTICATED, not on a login wall.

    ``has_cookies`` (a Cookies file exists) can't tell an expired session from a live one,
    so a profile marked "logged in" may still land signed out (the Amazon case: cookies
    from a login 11 days ago). We reuse the text-read util to ask the model whether the
    page shows a signed-in account or a sign-in prompt. Defaults to True on any ambiguity
    or read failure, so a flaky check never blocks a genuinely authenticated run."""
    url = ""
    with contextlib.suppress(Exception):
        url = st.page.url or ""
    if not url.startswith("http"):
        return True  # nothing navigated yet — don't false-positive on about:blank
    question = (
        "Look at this page's account/header area. Is a signed-in user account shown, or is "
        "it prompting to sign in / log in? Answer with exactly one word: SIGNED_IN or "
        "SIGNED_OUT."
    )
    ans = ""
    with contextlib.suppress(Exception):
        ans = await _read_page_answer(st.client, st.page, question, st.model_tag)
    return "SIGNED_OUT" not in ans.upper()


async def _launch(profile: str | None) -> tuple[Any, Any, Any]:
    """Launch a headless Chromium at Fara's viewport. Returns (pw, browser, page).

    With ``profile`` we reuse that profile's authenticated persistent context
    (browser is a persistent context; return it as both browser and context owner).
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    if profile:
        pdir = profiles.profile_dir(profile)
        pdir.mkdir(parents=True, exist_ok=True)
        # Shared anti-bot launcher: a saved login only stays valid headless on sites like
        # Amazon if the context looks like a real browser (see profiles.launch_persistent).
        context = await profiles.launch_persistent(pw, pdir, headless=True, viewport=VIEWPORT)
        page = context.pages[0] if context.pages else await context.new_page()
        return pw, context, page  # persistent context is its own "browser"
    # Public (no-profile) browse: same stealth so bot-sensitive sites don't serve a
    # degraded/blocked view.
    browser = await pw.chromium.launch(
        headless=True,
        args=profiles.STEALTH_ARGS,
        ignore_default_args=["--enable-automation"],
    )
    context = await browser.new_context(
        viewport=VIEWPORT,  # type: ignore[arg-type]  # plain dict vs ViewportSize TypedDict
        user_agent=profiles.FALLBACK_UA,
        locale="en-IN",
        timezone_id="Asia/Kolkata",
    )
    page = await context.new_page()
    return pw, browser, page


@dataclass
class _RunState:
    """All mutable state of one browse run — enough to pause and later resume it.

    On an ``ask_user_question`` the run stops with the browser (page/context) still
    **open** and this object stashed in ``_paused``; the user's reply is appended and the
    loop continues from ``step`` (see _drive/_pump/resume_paused). ADR 0016/0017 + the
    handover takeaway in evals/browse/E2E_CASES.md."""

    client: AsyncOpenAI
    model_tag: str
    pw: Any
    browser: Any
    page: Any
    live: Any
    attached: bool
    agent: FaraBrowseAgent
    messages: list[dict]
    trace_dir: Path | None
    # Original request, kept so a paused run can be *warm-relaunched* after a process
    # restart (the live browser can't survive one — see persistence helpers below).
    goal: str = ""
    profile: str | None = None
    start_url: str | None = None
    step: int = 0
    recent: list[str] = field(default_factory=list)
    last_click_bucket: tuple | None = None
    pending_obs: str = ""
    last_thoughts: str = ""
    # --- TEMP local diagnostic instrumentation (Case-1 timeout; remove after fix) ---
    # Wall-clock start of the run (perf_counter), so per-step trace can report cumulative
    # elapsed and the soft-deadline can end the run before the external tool cap fires.
    started_at: float = 0.0
    step_ms_log: list[int] = field(default_factory=list)
    llm_ms_log: list[int] = field(default_factory=list)


async def _new_state(goal: str, profile: str | None, start_url: str | None) -> _RunState:
    """Launch the browser, attach the live view, seed the conversation with the goal."""
    run_start = time.perf_counter()  # TEMP: aligns elapsed with the external tool budget
    client = AsyncOpenAI(
        base_url=config.settings.fara_base_url, api_key=config.settings.fara_api_key
    )
    model_tag, route_reason = _route_model(goal)
    log.info("fara_route", model=model_tag, reason=route_reason)

    live = live_browser.session()
    agent = FaraBrowseAgent()
    pw, browser, page = await _launch(profile)
    attached = False
    with contextlib.suppress(Exception):
        attached = await live.attach_page(page, agent)

    # Start on the profile's saved login URL (the right site + TLD, e.g. amazon.in not
    # .com) so the model begins authenticated on the correct page.
    if start_url:
        with contextlib.suppress(Exception):
            await page.goto(start_url, wait_until="domcontentloaded", timeout=30000)

    first_shot = await _screenshot(page)
    messages: list[dict] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": _b64_data_uri(first_shot)}},
                {
                    "type": "text",
                    "text": goal
                    + _TASK_HINT
                    + _SAFETY_SUFFIX
                    + _VERIFY_SUFFIX
                    + _ALTERNATIVES_SUFFIX,
                },
            ],
        },
    ]

    trace_dir: Path | None = None
    if config.settings.fara_save_traces:
        trace_dir = config.DATA_DIR / "fara_traces" / uuid.uuid4().hex[:8]
        trace_dir.mkdir(parents=True, exist_ok=True)
        (trace_dir / "goal.txt").write_text(goal, encoding="utf-8")
        (trace_dir / "screenshot_00_start.png").write_bytes(first_shot)
        log.info("fara_trace_dir", dir=str(trace_dir))

    return _RunState(
        client=client,
        model_tag=model_tag,
        pw=pw,
        browser=browser,
        page=page,
        live=live,
        attached=attached,
        agent=agent,
        messages=messages,
        trace_dir=trace_dir,
        goal=goal,
        profile=profile,
        start_url=start_url,
        started_at=run_start,
    )


async def _append_user_reply(st: _RunState, reply: str) -> None:
    """Resume seed: append the user's answer + a fresh screenshot of the paused page."""
    shot = await _screenshot(st.page)
    url = ""
    with contextlib.suppress(Exception):
        url = st.page.url
    prefix = f"Current URL: {url}\n" if url else ""
    st.messages.append(
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": _b64_data_uri(shot)}},
                {"type": "text", "text": f"{prefix}{reply}\n{USER_MESSAGE}"},
            ],
        }
    )
    _trim_images(st.messages, MAX_IMAGES)


def _write_summary(st: _RunState, outcome: str, final: str) -> None:
    """TEMP local diagnostic: write summary.json for one run's outcome + timing rollup.

    Called at every terminal path (done/paused/step_limit/deadline/error) so a run always
    leaves a record — unlike today's external hard-cancel, which leaves no trace tail."""
    if st.trace_dir is None:
        return
    elapsed = round(time.perf_counter() - st.started_at, 1) if st.started_at else None
    steps, llms = st.step_ms_log, st.llm_ms_log
    summary = {
        "outcome": outcome,
        "goal": st.goal,
        "profile": st.profile,
        "model_tag": st.model_tag,
        "n_steps": st.step,
        "total_elapsed_s": elapsed,
        "avg_step_ms": round(sum(steps) / len(steps)) if steps else None,
        "max_step_ms": max(steps) if steps else None,
        "avg_llm_ms": round(sum(llms) / len(llms)) if llms else None,
        "slowest_step": (steps.index(max(steps)) + 1) if steps else None,
        "final": final[:500],
    }
    with contextlib.suppress(Exception):
        (st.trace_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


async def _drive(st: _RunState) -> tuple[str, str]:
    """Run the observe-think-act loop from ``st.step``.

    Returns ``("paused", question)`` when the model asks the user something (browser
    left open), or ``("done", answer)`` on terminate / step-limit. Raises _Unavailable
    if the model host drops. Never closes the browser — the caller (_pump/_run) does."""
    # Authenticated run, fresh start: verify we actually landed signed in. A stale profile
    # (expired cookies) lands on a login wall; proceeding there makes the model act
    # logged-out and hallucinate success (e.g. "added to cart" with an empty cart). Instead,
    # stop and hand the user back to re-login. Skipped on resume (step > 0).
    if st.profile and st.step == 0 and not await _signed_in(st):
        msg = profiles.relogin_message(st.profile)
        log.warning("fara_signed_out", profile=st.profile)
        _write_summary(st, "login_needed", msg)
        return "done", msg

    while st.step < MAX_STEPS:
        # TEMP soft-deadline: end gracefully with a recorded summary + partial answer
        # BEFORE the external tool timeout hard-cancels us (leaving no trace).
        budget = config.settings.fara_deadline_seconds
        if budget and st.started_at:
            elapsed_now = round(time.perf_counter() - st.started_at, 1)
            if elapsed_now >= budget:
                payload = (
                    f"I ran out of time after {elapsed_now}s at step {st.step}. "
                    f"Last thing I saw: {st.last_thoughts[:300]}"
                )
                log.warning("fara_deadline", elapsed_s=elapsed_now, step=st.step)
                _write_summary(st, "deadline", payload)
                return "done", payload

        st.step += 1
        step = st.step
        step_start = time.perf_counter()
        await st.agent.wait_if_paused()  # block here while the user has take-control

        t_llm = time.perf_counter()
        try:
            resp = await st.client.chat.completions.create(
                model=st.model_tag,
                messages=st.messages,  # type: ignore[arg-type]  # our dicts vs OpenAI param types
                temperature=0,
                max_tokens=1024,
            )
        except Exception as e:  # noqa: BLE001
            raise _Unavailable(str(e)) from e
        llm_ms = round((time.perf_counter() - t_llm) * 1000)

        text = resp.choices[0].message.content or ""
        st.messages.append({"role": "assistant", "content": text})
        thoughts, args = _parse(text)
        st.last_thoughts = thoughts or st.last_thoughts
        action = args.get("action", "terminate")
        # Stall detector: the same action *type* three steps running (regardless of exact
        # coords) is the form/search-box failure mode — catch it and nudge.
        st.recent.append(action)
        st.recent = st.recent[-3:]
        stalled = len(st.recent) == 3 and len(set(st.recent)) == 1
        # Dead-click loop: the EXACT same spot twice means the element isn't responding.
        click_bucket = (
            tuple(round(c / 30) for c in (args.get("coordinate") or []))
            if action == "left_click"
            else None
        )
        same_click = click_bucket is not None and click_bucket == st.last_click_bucket
        st.last_click_bucket = click_bucket
        detail = args.get("url") or args.get("query") or args.get("text") or args.get("answer")
        log.info(
            "fara_step",
            step=step,
            action=action,
            coord=args.get("coordinate"),
            detail=(str(detail)[:80] if detail else None),
            stalled=stalled,
            same_click=same_click,
            llm_ms=llm_ms,  # TEMP: model latency + cumulative elapsed for timeout diagnosis
            elapsed_s=(round(time.perf_counter() - st.started_at, 1) if st.started_at else 0.0),
            thought=(thoughts or text.strip().splitlines()[0] if text.strip() else "")[:140],
        )

        st.llm_ms_log.append(llm_ms)  # TEMP: every model call, incl. terminal steps
        t_act = time.perf_counter()
        is_terminal, obs = await _dispatch(st.page, action, args)
        if isinstance(obs, str) and obs.startswith("__READ__:"):
            answer = await _read_page_answer(
                st.client, st.page, obs[len("__READ__:") :], st.model_tag
            )
            st.pending_obs = f"Read the page: {answer}"
            is_terminal, obs = False, st.pending_obs
        act_ms = round((time.perf_counter() - t_act) * 1000)
        if is_terminal:
            if isinstance(obs, str) and obs.startswith("__ASK__:"):
                question = (obs[len("__ASK__:") :].strip() or thoughts) or (
                    "I need a bit more information to continue."
                )
                _write_summary(st, "paused", question)
                return "paused", question
            answer = obs or thoughts or "(done)"
            _write_summary(st, "done", answer)
            return "done", answer

        # Break a dead-click loop by physically changing the view so the next screenshot
        # differs and the model re-grounds instead of re-clicking.
        t_shot = time.perf_counter()
        if same_click:
            with contextlib.suppress(Exception):
                await st.page.mouse.wheel(0, 500)

        with contextlib.suppress(Exception):
            await st.page.wait_for_load_state("domcontentloaded", timeout=8000)

        shot = await _screenshot(st.page)
        url = ""
        with contextlib.suppress(Exception):
            url = st.page.url
        shot_ms = round((time.perf_counter() - t_shot) * 1000)
        step_ms = round((time.perf_counter() - step_start) * 1000)
        elapsed_s = round(time.perf_counter() - st.started_at, 1) if st.started_at else 0.0
        st.step_ms_log.append(step_ms)
        prefix = f"Current URL: {url}\n" if url else ""
        if same_click:
            stuck = (
                "You clicked the EXACT same spot again and nothing changed. Do NOT click "
                "there again — that element isn't responding or was mis-located. The view "
                "has been scrolled; re-read the screenshot and click a clearly DIFFERENT "
                "element, or scroll to bring it into view.\n"
            )
        elif stalled:
            stuck = _STUCK_HINTS.get(action, _STUCK_HINT) + "\n"
        else:
            stuck = ""
        note = stuck + ((st.pending_obs + "\n") if st.pending_obs else "")
        st.pending_obs = ""

        if st.trace_dir is not None:
            with contextlib.suppress(Exception):
                (st.trace_dir / f"screenshot_{step:02d}.png").write_bytes(shot)
                with (st.trace_dir / "steps.jsonl").open("a", encoding="utf-8") as f:
                    f.write(
                        json.dumps(
                            {
                                "step": step,
                                "action": action,
                                "coord": args.get("coordinate"),
                                "detail": (str(detail)[:120] if detail else None),
                                "stalled": stalled,
                                "same_click": same_click,
                                "url": url,
                                # TEMP timing breakdown for the Case-1 timeout diagnosis
                                "llm_ms": llm_ms,
                                "act_ms": act_ms,
                                "shot_ms": shot_ms,
                                "step_ms": step_ms,
                                "elapsed_s": elapsed_s,
                                "thought": (thoughts or "")[:500],
                            }
                        )
                        + "\n"
                    )
        st.messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _b64_data_uri(shot)}},
                    {"type": "text", "text": f"{prefix}{note}{USER_MESSAGE}"},
                ],
            }
        )
        _trim_images(st.messages, MAX_IMAGES)

    payload = (
        "I couldn't finish within the step limit. Here's the last thing I saw: "
        f"{st.last_thoughts[:300]}"
    )
    _write_summary(st, "step_limit", payload)
    return "done", payload


async def _close(st: _RunState) -> None:
    """Tear down the browser + live-view attachment for a finished (not paused) run."""
    if st.attached:
        with contextlib.suppress(Exception):
            await st.live.detach()
    with contextlib.suppress(Exception):
        if st.browser is not None:
            await st.browser.close()  # persistent context or browser both have close()
    with contextlib.suppress(Exception):
        if st.pw is not None:
            await st.pw.stop()


async def _run(goal: str, profile: str | None, start_url: str | None = None) -> str:
    """Single-shot, NON-resumable drive (evals / back-compat).

    An ``ask_user_question`` returns the question text and ends the run — there is no
    interactive user here. The resumable production path is ``browse_fara``."""
    st = await _new_state(goal, profile, start_url)
    try:
        _kind, payload = await _drive(st)
        return payload
    finally:
        await _close(st)


def _unavailable_message() -> str:
    msg = (
        "The live browsing agent is offline right now — the local Fara vision model "
        "host isn't reachable. Computer-use runs only on that local, open-source model "
        "(reached over Tailscale in production), which may be asleep or disconnected."
    )
    url = config.settings.fara_unavailable_url
    if url:
        # Markdown link so the UI renders it as a clickable "watch the demo" affordance —
        # e.g. for a recruiter checking how the browsing workflow looks (see ADR 0017).
        msg += f" [Watch a recorded walkthrough of this workflow]({url})"
    return msg


# One headless Chromium at a time keeps memory sane on a small VM.
_lock = asyncio.Lock()

# A run paused at an ask_user_question, its browser still open, awaiting the user's reply.
# Single-user app → at most one paused browse at a time (the lock serializes browses).
# This global IS the handover signal: the turn layer checks has_paused_browse() and routes
# the next user message to resume_paused_browse() instead of starting a fresh turn.
_paused: _RunState | None = None


def has_paused_browse() -> bool:
    """True when a browse is paused waiting for the user to answer a question."""
    return _paused is not None


async def discard_paused_browse() -> None:
    """Abandon a paused browse (e.g. user moved on) and free its browser + saved record."""
    global _paused
    if _paused is not None:
        st, _paused = _paused, None
        with contextlib.suppress(Exception):
            await _close(st)
        log.info("fara_paused_discarded")
    _clear_handover()


async def _pump(st: _RunState) -> str:
    """Drive ``st`` to a pause or a finish, handling teardown + failure uniformly.

    On pause: stash ``st`` in ``_paused`` (browser stays open) and persist a warm-relaunch
    record. On any terminal outcome (finish OR error): close the browser and clear the
    persisted record, so a failed run can't leave a stale handover that hijacks the next
    message."""
    global _paused
    try:
        kind, payload = await _drive(st)
    except _Unavailable as e:
        log.warning("fara_unavailable", error=str(e))
        _write_summary(st, "error", f"unavailable: {e}")  # TEMP diagnostic
        await _close(st)
        _clear_handover()
        return _unavailable_message()
    except Exception as e:  # noqa: BLE001
        log.warning("fara_browse_failed", error=str(e))
        _write_summary(st, "error", f"{type(e).__name__}: {e}")  # TEMP diagnostic
        await _close(st)
        _clear_handover()
        return f"browse failed: {e}"
    if kind == "paused":
        _paused = st
        _persist_handover(st, payload)  # survive a restart as a warm relaunch
        log.info("fara_paused_for_user", step=st.step, question=payload[:120])
        return payload
    await _close(st)
    _clear_handover()
    return payload


async def browse_fara(goal: str, profile: str | None = None, start_url: str | None = None) -> str:
    """Drive a browser toward ``goal`` with the native Fara-1.5 loop (no fallback).

    Resumable: if the model asks the user something, the run pauses with the browser open
    (``has_paused_browse()`` becomes true) and returns the question; the next user message
    resumes it via :func:`resume_paused_browse`."""
    # A brand-new browse supersedes any stale paused one (user started something else).
    await discard_paused_browse()
    async with _lock:
        try:
            st = await _new_state(goal, profile, start_url)
        except _Unavailable as e:
            log.warning("fara_unavailable", error=str(e))
            return _unavailable_message()
        except Exception as e:  # noqa: BLE001
            log.warning("fara_browse_failed", error=str(e))
            return f"browse failed: {e}"
        return await _pump(st)


async def resume_paused_browse(reply: str) -> str:
    """Resume the paused browse with the user's ``reply``; returns the next result.

    May itself pause again (another question) or finish. If nothing is paused, says so."""
    global _paused
    if _paused is None:
        return "There's no browsing task waiting on your input right now."
    async with _lock:
        st, _paused = _paused, None
        try:
            await _append_user_reply(st, reply)
        except Exception as e:  # noqa: BLE001 — page died while paused
            log.warning("fara_resume_failed", error=str(e))
            await _close(st)
            _clear_handover()
            return "I lost the browser session while waiting — please ask me again."
        return await _pump(st)


# --- Cross-restart persistence (ADR 0017 #3) ------------------------------------------
# The live browser can't survive a process restart, so we can't literally resume the same
# page. Instead, on pause we persist a lightweight record of the handover; after a restart
# the user's reply triggers a *warm relaunch* — a fresh browse seeded with the original
# goal, the question we asked, the reply, and the last URL. A JSON file (not a DB table)
# keeps this migration-free and matches the single-user / one-browse-at-a-time model.
_HANDOVER_FILE = config.DATA_DIR / "pending_handover.json"


def _persist_handover(st: _RunState, question: str) -> None:
    last_url = ""
    with contextlib.suppress(Exception):
        last_url = st.page.url
    record = {
        "goal": st.goal,
        "profile": st.profile,
        "start_url": st.start_url,
        "last_url": last_url,
        "question": question,
        "ts": time.time(),
    }
    with contextlib.suppress(Exception):
        _HANDOVER_FILE.write_text(json.dumps(record), encoding="utf-8")


def _load_handover() -> dict | None:
    if not _HANDOVER_FILE.exists():
        return None
    try:
        return json.loads(_HANDOVER_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt record is as good as none
        return None


def _clear_handover() -> None:
    with contextlib.suppress(Exception):
        _HANDOVER_FILE.unlink(missing_ok=True)


def has_persisted_handover() -> bool:
    """True when a handover was saved to disk but its live run is gone (e.g. a restart)."""
    return _paused is None and _HANDOVER_FILE.exists()


def has_pending_browse() -> bool:
    """True when the next user message should resume a browse — live OR warm-relaunched."""
    return has_paused_browse() or has_persisted_handover()


async def resume_after_restart(reply: str) -> str:
    """Warm-relaunch a browse whose live session was lost, seeded with the saved context."""
    record = _load_handover()
    _clear_handover()
    if not record:
        return "There's no browsing task waiting on your input right now."
    goal = str(record.get("goal") or "").strip()
    question = str(record.get("question") or "").strip()
    last_url = record.get("last_url") or record.get("start_url") or None
    combined = (
        f"{goal}\n\n(Resuming after an interruption. I had paused to ask: "
        f'"{question}" — the user answered: "{reply}". Continue the task with that answer'
        + (f"; the relevant page was {last_url}." if last_url else ".")
        + ")"
    )
    log.info("fara_warm_relaunch", last_url=last_url or "-")
    return await browse_fara(combined, profile=record.get("profile"), start_url=last_url)


async def resume_pending_browse(reply: str) -> str:
    """Resume a paused browse: live in-memory if present, else a warm relaunch from disk."""
    if has_paused_browse():
        return await resume_paused_browse(reply)
    if has_persisted_handover():
        return await resume_after_restart(reply)
    return "There's no browsing task waiting on your input right now."
