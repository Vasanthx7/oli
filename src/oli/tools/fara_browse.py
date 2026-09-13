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
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from openai import AsyncOpenAI

from .. import config, live_browser, profiles
from ..logging_config import get_logger

log = get_logger(__name__)

# Fara's trained operating point. 1440×900 gives the most reliable grounding;
# coordinates come back in a normalized 0–DISPLAY_SIZE space and scale to pixels.
VIEWPORT = {"width": 1440, "height": 900}
DISPLAY_SIZE = 1000
MAX_STEPS = 12
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

_SYSTEM_PROMPT = (Path(__file__).parent / "fara_system_prompt.txt").read_text(encoding="utf-8")

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
        target = url if "://" in url else "https://" + url
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
        # Single-user autonomous run: surface the question to the chat agent.
        return True, f"I need input to continue: {args.get('question', '')}"
    if action == "terminate":
        return True, str(args.get("answer", args.get("thoughts", "")))

    return False, f"(unsupported action: {action})"


async def _read_page_answer(client: AsyncOpenAI, page: Any, question: str) -> str:
    """Fara's read_page_answer_question: pull page text and answer with the model."""
    text = ""
    with contextlib.suppress(Exception):
        text = await page.evaluate("document.body.innerText")
    text = (text or "")[:8000]
    with contextlib.suppress(Exception):
        r = await client.chat.completions.create(
            model=config.settings.fara_model,
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
        context = await pw.chromium.launch_persistent_context(
            str(pdir),
            headless=True,
            viewport=VIEWPORT,  # type: ignore[arg-type]  # plain dict vs ViewportSize TypedDict
        )
        page = context.pages[0] if context.pages else await context.new_page()
        return pw, context, page  # persistent context is its own "browser"
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(viewport=VIEWPORT)  # type: ignore[arg-type]
    page = await context.new_page()
    return pw, browser, page


async def _run(goal: str, profile: str | None) -> str:
    client = AsyncOpenAI(
        base_url=config.settings.fara_base_url, api_key=config.settings.fara_api_key
    )

    pw = browser = page = None
    live = live_browser.session()
    attached = False
    agent = FaraBrowseAgent()
    try:
        pw, browser, page = await _launch(profile)
        with contextlib.suppress(Exception):
            attached = await live.attach_page(page, agent)

        first_shot = await _screenshot(page)
        messages: list[dict] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": _b64_data_uri(first_shot)}},
                    {"type": "text", "text": goal + _TASK_HINT},
                ],
            },
        ]

        pending_obs = ""
        recent: list[str] = []  # recent action names, for stall detection
        for _step in range(MAX_STEPS):
            await agent.wait_if_paused()  # block here while the user has control

            try:
                resp = await client.chat.completions.create(
                    model=config.settings.fara_model,
                    messages=messages,  # type: ignore[arg-type]  # our dicts vs OpenAI param types
                    temperature=0,
                    max_tokens=1024,
                )
            except Exception as e:  # noqa: BLE001
                raise _Unavailable(str(e)) from e

            text = resp.choices[0].message.content or ""
            messages.append({"role": "assistant", "content": text})
            thoughts, args = _parse(text)
            action = args.get("action", "terminate")
            # Stall detector: the same action *type* three steps running (regardless of
            # exact coords) is the form/search-box failure mode — e.g. scrolling around
            # hunting for a submit button. Catch it and nudge, don't burn the budget.
            recent.append(action)
            recent = recent[-3:]
            stalled = len(recent) == 3 and len(set(recent)) == 1
            log.info(
                "fara_step",
                step=_step + 1,
                action=action,
                coord=args.get("coordinate"),
                stalled=stalled,
                thought=thoughts[:140],
            )

            is_terminal, obs = await _dispatch(page, action, args)
            if isinstance(obs, str) and obs.startswith("__READ__:"):
                answer = await _read_page_answer(client, page, obs[len("__READ__:") :])
                pending_obs = f"Read the page: {answer}"
                is_terminal, obs = False, pending_obs
            if is_terminal:
                return obs or thoughts or "(done)"

            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=8000)

            shot = await _screenshot(page)
            url = ""
            with contextlib.suppress(Exception):
                url = page.url
            prefix = f"Current URL: {url}\n" if url else ""
            stuck = (_STUCK_HINTS.get(action, _STUCK_HINT) + "\n") if stalled else ""
            note = stuck + ((pending_obs + "\n") if pending_obs else "")
            pending_obs = ""
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": _b64_data_uri(shot)}},
                        {"type": "text", "text": f"{prefix}{note}{USER_MESSAGE}"},
                    ],
                }
            )
            _trim_images(messages, MAX_IMAGES)

        return (
            "I couldn't finish within the step limit. Here's the last thing I saw: "
            f"{thoughts[:300]}"
        )
    finally:
        if attached:
            with contextlib.suppress(Exception):
                await live.detach()
        with contextlib.suppress(Exception):
            if browser is not None:
                await browser.close()  # persistent context or browser both have close()
        with contextlib.suppress(Exception):
            if pw is not None:
                await pw.stop()


def _unavailable_message() -> str:
    msg = (
        "The browsing agent is offline right now — the Fara model host isn't "
        "reachable. It runs on a local machine (reached over Tailscale in "
        "production), which may be asleep or disconnected."
    )
    url = config.settings.fara_unavailable_url
    if url:
        msg += f" You can watch a recorded walkthrough of this workflow here: {url}"
    return msg


# One headless Chromium at a time keeps memory sane on a small VM.
_lock = asyncio.Lock()


async def browse_fara(goal: str, profile: str | None = None) -> str:
    """Drive a browser toward ``goal`` with the native Fara-1.5 loop (no fallback)."""
    async with _lock:
        try:
            return await _run(goal, profile)
        except _Unavailable as e:
            log.warning("fara_unavailable", error=str(e))
            return _unavailable_message()
        except Exception as e:  # noqa: BLE001
            log.warning("fara_browse_failed", error=str(e))
            return f"browse failed: {e}"
