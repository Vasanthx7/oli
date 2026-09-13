"""A/B counterpart to ``run_fara.py``: drive OLI's **vendored** Fara loop.

Where ``run_fara.py`` runs Microsoft's official harness (``fara.agents.Fara15Agent``
+ its ``PlaywrightEnvironment``) in an isolated Fara venv, this file drives the loop
oli actually ships in production — ``oli.tools.fara_browse`` (ADR 0016) — over the
**same graded scenarios**, the **same Ollama model**, and the **same RunResult schema**
and reporting helpers (imported from ``run_fara``). That makes the two result JSONs
diff 1:1 and answers the one question the model card raises: *does our reimplementation
of the loop match the co-designed harness on our hardware?* (see ADR 0017 review).

It reuses the vendored loop unmodified. To recover per-step model latency and the
action fingerprint (which ``fara_browse`` doesn't return), the runner monkeypatches the
OpenAI client the loop constructs, timing each ``chat.completions.create`` and parsing
the emitted action with the loop's own ``_parse`` — eval-only, production untouched.

Run from the repo root, in the OLI venv, with Ollama serving the model:

    ollama serve                       # (on the GPU box)
    uv run python -m evals.browse.run_vendored --max-tier 2
    uv run python -m evals.browse.run_vendored --ids t1-example-heading --stamp vendored-latest

Results print as a table + summary and are written to
``evals/browse/results/<stamp>.json`` (default stamp ``vendored-latest``), matching
``run_fara.py`` so ``compare.py`` (or a plain diff) can line them up.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import time
import uuid
from dataclasses import asdict
from typing import Any

from ._resources import ResourceSampler
from .run_fara import (
    RESULTS_DIR,
    RunResult,
    _categorize,
    _fmt_table,
    _max_repeated_action,
    _summary,
)
from .scenarios import Scenario, by_tier

# Defaults mirror run_fara so the fight is fair. The step ceiling is the vendored
# loop's OWN production ceiling (oli.tools.fara_browse.MAX_STEPS); note it differs
# from run_fara's MAX_ROUNDS=12 — each engine runs at the budget it ships with, which
# is the honest comparison. Override the model/URL to match whatever Ollama serves.
MODEL = "fara15-4b"
BASE_URL = "http://localhost:11434/v1"

# Sentinel the vendored loop returns when it exhausts its step budget (not "done").
_STEP_LIMIT_PREFIX = "I couldn't finish within the step limit"


def _install_timing_probe(fb: Any, latencies: list[float], actions: list[str]) -> None:
    """Monkeypatch fb.AsyncOpenAI to time each model call and record its action.

    The vendored loop does ``client = AsyncOpenAI(...)`` then
    ``client.chat.completions.create(...)`` once per step; wrapping that call gives us
    per-step latency and (via the loop's own ``_parse``) the action fingerprint —
    without changing any production code.
    """
    from openai import AsyncOpenAI as _RealClient

    class _TimedCompletions:
        def __init__(self, real: Any) -> None:
            self._real = real

        async def create(self, *a: Any, **k: Any) -> Any:
            t0 = time.perf_counter()
            resp = await self._real.chat.completions.create(*a, **k)
            latencies.append(round(time.perf_counter() - t0, 3))
            with contextlib.suppress(Exception):
                _, args = fb._parse(resp.choices[0].message.content or "")
                actions.append(str(args.get("action", "?")))
            return resp

    class _TimedChat:
        def __init__(self, real: Any) -> None:
            self.completions = _TimedCompletions(real)

    class _TimedClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            self._real = _RealClient(*a, **k)
            self.chat = _TimedChat(self._real)

    fb.AsyncOpenAI = _TimedClient  # type: ignore[attr-defined]


async def _run_one(scn: Scenario, attempt: int, model: str, max_steps: int) -> RunResult:
    from oli.tools import fara_browse as fb

    latencies: list[float] = []
    actions: list[str] = []
    _install_timing_probe(fb, latencies, actions)

    started = time.monotonic()
    run_error = ""
    final = ""
    is_done = False
    sampler = ResourceSampler(interval=0.5)
    with contextlib.suppress(Exception):
        sampler.__enter__()
    try:
        # Call the loop directly (not browse_fara) so we categorize errors ourselves,
        # exactly like run_fara catches into run_error.
        final = (await fb._run(scn.goal, None, None)) or ""
        final = final.strip()
        is_done = bool(final) and not final.startswith(_STEP_LIMIT_PREFIX)
    except fb._Unavailable as e:  # model host unreachable
        run_error = f"Unavailable: {e}"
    except Exception as e:  # noqa: BLE001
        run_error = f"{type(e).__name__}: {e}"
    finally:
        with contextlib.suppress(Exception):
            sampler.__exit__()

    wall = round(time.monotonic() - started, 2)
    res_stats: dict = {}
    with contextlib.suppress(Exception):
        res_stats = sampler.stats()

    correct = False
    with contextlib.suppress(Exception):
        correct = bool(scn.validate(final.lower()))

    n_steps = len(latencies)
    category = _categorize(
        correct=correct,
        is_done=is_done,
        n_steps=n_steps,
        run_error=run_error,
        max_rounds=max_steps,
    )
    return RunResult(
        scenario_id=scn.id,
        tier=scn.tier,
        attempt=attempt,
        success=correct and is_done,
        correct=correct,
        is_done=is_done,
        total_seconds=wall,
        n_steps=n_steps,
        per_step_seconds=latencies,
        max_repeated_action=_max_repeated_action(actions),
        failure_category=category,
        final_result=final[:500],
        error=run_error,
        actions=actions,
        resources=res_stats,
    )


async def _main_async(args: argparse.Namespace) -> int:
    from oli.config import settings

    model = args.model or MODEL
    # Force parity: one fixed model, no auto-routing, point at the chosen Ollama.
    settings.fara_model = model
    settings.fara_autoroute = False
    if args.base_url:
        settings.fara_base_url = args.base_url
    settings.fara_save_traces = False

    from oli.tools import fara_browse as fb

    max_steps = int(fb.MAX_STEPS)

    ids = [s.strip() for s in args.ids.split(",") if s.strip()] or None
    scenarios = by_tier(args.max_tier, ids)
    if not scenarios:
        print("no scenarios matched")
        return 1

    print(
        f"vendored loop | model={model} | base_url={settings.fara_base_url} | max_steps={max_steps}"
    )
    print(f"running {len(scenarios)} scenario(s) x {args.repeat} …\n")

    results: list[RunResult] = []
    for scn in scenarios:
        for attempt in range(1, args.repeat + 1):
            r = await _run_one(scn, attempt, model, max_steps)
            tag = "PASS" if r.success else "FAIL"
            print(
                f"  [{tag}] {r.scenario_id} (#{attempt}) "
                f"— {r.failure_category} — {r.total_seconds}s"
            )
            results.append(r)

    print("\n" + _fmt_table(results))
    print(_summary(results))

    stamp = args.stamp or "vendored-latest"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{stamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "harness": "vendored",
                "engine": "oli.tools.fara_browse",
                "model": model,
                "base_url": settings.fara_base_url,
                "max_steps": max_steps,
                "run_id": uuid.uuid4().hex[:8],
                "results": [asdict(r) for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {out_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="OLI vendored Fara loop harness (A/B vs run_fara)")
    p.add_argument("--max-tier", type=int, default=None, help="run scenarios up to this tier (1-4)")
    p.add_argument("--ids", type=str, default="", help="comma-separated scenario ids to run")
    p.add_argument("--repeat", type=int, default=1, help="run each scenario N times")
    p.add_argument(
        "--stamp", type=str, default="", help="results filename stamp; default vendored-latest"
    )
    p.add_argument("--model", type=str, default="", help="Ollama model tag (default fara15-4b)")
    p.add_argument(
        "--base-url", type=str, default="", help="override the OpenAI-compatible base URL"
    )
    args = p.parse_args()

    # Windows needs the Proactor loop for Playwright subprocesses (see oli.main).
    with contextlib.suppress(Exception):
        import sys

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
