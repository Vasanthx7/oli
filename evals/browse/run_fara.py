"""Fara-1.5 spike harness — the *native* Fara runtime, NOT browser-use.

This is the counterpart to ``run.py``. Where ``run.py`` drives the production
browser-use ``Agent`` with the local vision model (qwen2.5vl:7b), this file drives
Microsoft's own Fara-1.5 agent loop (``fara.agents.fara.Fara15Agent`` + its
``PlaywrightEnvironment``) against a quantized Fara-1.5-4B served by Ollama.

The point of the spike is the one thing desk research couldn't answer: does a
Q4 Fara-4B, run through its own observe-think-act coordinate loop, beat our
current local setup on *our* hardware — without being too slow?

To keep the comparison honest it reuses the **same graded scenarios and
validators** as ``run.py`` (``scenarios.py`` has no oli/browser-use imports, so
it loads fine in the isolated Fara venv) and reports the same metric families:

  * success   — finished AND validator-correct
  * latency   — total wall-clock and per-step *model* seconds (we time each
                chat.completions call directly, since Fara's loop doesn't expose
                StepMetadata the way browser-use does)
  * steps     — number of model rounds, and the longest run of one repeated action
  * failure   — a coarse category (parse-error / navigation / incomplete / …)

Run it with the Fara venv's python, from the repo root, with Ollama serving the
model:

    ollama pull hf.co/bartowski/Fara1.5-4B-GGUF:Q4_K_M
    <fara-venv>/Scripts/python -m evals.browse.run_fara --max-tier 2
    <fara-venv>/Scripts/python -m evals.browse.run_fara --ids t1-example-heading

Results print as a table + summary and are written to
``evals/browse/results/<stamp>.json`` (default stamp ``fara-latest``).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ._resources import ResourceSampler, fmt_stats
from .scenarios import Scenario, by_tier

# Defaults chosen to mirror production browse where it matters for a fair fight:
# the step ceiling is the same 12 browse uses (oli.tools.browse.MAX_STEPS), so
# "ran out of budget" means the same thing in both harnesses.
# Built locally from bartowski/Fara1.5-4B-GGUF:Q4_K_M blobs (model + mmproj) via a
# Modelfile — the direct `ollama pull hf.co/...` kept timing out on HF's manifest
# fetch while the blobs themselves downloaded fine. Same weights, local tag.
MODEL = "fara15-4b"
BASE_URL = "http://localhost:11434/v1"
API_KEY = "ollama"  # Ollama ignores the key but the OpenAI client requires one.
MAX_ROUNDS = 12
VIEWPORT = {"width": 1440, "height": 900}  # Fara's native display geometry.
HEADLESS = True  # flip with --headful to watch the browser drive itself

RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class RunResult:
    scenario_id: str
    tier: int
    attempt: int
    success: bool
    correct: bool
    is_done: bool
    total_seconds: float
    n_steps: int
    per_step_seconds: list[float]  # per model-call latency (the speed metric)
    max_repeated_action: int
    failure_category: str
    final_result: str
    error: str = ""
    actions: list[str] = field(default_factory=list)
    resources: dict = field(default_factory=dict)  # GPU/CPU/RAM peak+avg


def _max_repeated_action(action_names: list[str]) -> int:
    """Longest streak of the same action name back-to-back — the loop fingerprint."""
    best = cur = 0
    prev = None
    for s in action_names:
        cur = cur + 1 if s == prev else 1
        best = max(best, cur)
        prev = s
    return best


def _categorize(
    *,
    correct: bool,
    is_done: bool,
    n_steps: int,
    run_error: str,
    max_rounds: int,
) -> str:
    low = run_error.lower()
    if any(k in low for k in ("out of memory", "insufficient memory", "cuda")) or re.search(
        r"\boom\b", low
    ):
        return "oom"
    if any(k in low for k in ("invalid json", "expecting value", "jsondecode", "parse")):
        return "parse-error"
    if any(k in low for k in ("timeout", "timed out", "deadline")):
        return "timeout"
    if any(k in low for k in ("connection", "refused", "econnrefused", "cannot connect")):
        return "connection"
    if any(
        k in low
        for k in ("err_name_not_resolved", "err_connection", "net::", "err_aborted", "goto")
    ):
        return "navigation"
    if run_error:
        return "error"
    if not is_done and n_steps >= max_rounds:
        return "incomplete-max-rounds"
    if is_done and not correct:
        return "wrong-answer"
    if not is_done:
        return "incomplete"
    return "ok"


def _build_timed_agent_cls() -> type:
    """Subclass Fara15Agent to record the wall time of every model call.

    Fara's loop doesn't surface per-step timing (browser-use does via
    StepMetadata), so we wrap the one method that makes the chat.completions
    request. This is the seconds/step number the spike is really about.
    """
    from fara.agents.fara.fara15_agent import Fara15Agent

    class TimedFara15Agent(Fara15Agent):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, **kw)
            self.call_latencies: list[float] = []

        async def _make_model_call(self, history, extra_create_args=None):  # type: ignore[override]
            t0 = time.monotonic()
            try:
                return await super()._make_model_call(history, extra_create_args)
            finally:
                self.call_latencies.append(round(time.monotonic() - t0, 2))

    return TimedFara15Agent


async def _run_one(scn: Scenario, attempt: int, agent_cls: type, cfg_cls: type) -> RunResult:
    from fara.core.data_point import SolverStatus, Task
    from fara.core.run_context import RunContext
    from fara.environments.playwright import PlaywrightEnvironment

    endpoint = {"model": MODEL, "base_url": BASE_URL, "api_key": API_KEY}

    env = PlaywrightEnvironment(
        viewport_width=VIEWPORT["width"],
        viewport_height=VIEWPORT["height"],
        headless=HEADLESS,
        browser_channel="chromium",
        start_page="about:blank",  # force the agent to navigate itself, like browse
        single_tab_mode=True,
        use_browserbase=False,
    )

    agent = agent_cls(
        cfg_cls(
            client_config=endpoint,
            max_rounds=MAX_ROUNDS,
            identity="fara_qwen35",
            critical_points="fara-1.5",
            save_screenshots=False,
            # No Browserbase session -> skip the captcha gate entirely, else every
            # step waits on a solver that can never run.
            captcha_timeout_limit=0,
            # Eval mode: don't halt on ask_user_question, inject a dummy "continue".
            auto_user_reply=True,
            # A malformed tool_call ends the trajectory with the raw text as the
            # answer instead of crashing the whole run — we'd rather grade it.
            terminate_on_parse_error=True,
        )
    )

    started = time.monotonic()
    run_error = ""
    final_answer = ""
    is_done = False
    action_names: list[str] = []
    sampler = ResourceSampler(interval=0.5)
    sampler.__enter__()  # sample GPU/CPU/RAM for the whole scenario

    try:
        await env.initialize()
        task = Task(task_id=uuid.uuid4().hex, instruction=scn.goal)
        # Unique dir per run: RunContext.create auto-RESUMES any existing dir
        # (reloading the old task.json + events), so a stable name would silently
        # replay the previous run instead of the current goal.
        output_dir = RESULTS_DIR / "_traces" / f"{scn.id}_{attempt}_{task.task_id[:8]}"
        run_context = RunContext.create(environment=env, task=task, output_dir=output_dir)
        await agent.initialize(run_context)
        try:
            final_answer, all_actions, _obs = await agent.run(run_context)
            is_done = run_context.solver_log.status == SolverStatus.COMPLETE
            # Recover the per-round action names for the loop fingerprint.
            for ev in run_context.data_point.solver_log.events:
                name = getattr(ev, "action_name", None)
                if name:
                    action_names.append(name)
        finally:
            with contextlib.suppress(Exception):
                await agent.close(run_context)
    except Exception as e:  # noqa: BLE001
        run_error = f"{type(e).__name__}: {e}"
    finally:
        with contextlib.suppress(Exception):
            await env.close()
        sampler.__exit__()

    wall = round(time.monotonic() - started, 2)
    res_stats = sampler.stats()
    final = (final_answer or "").strip()
    correct = False
    with contextlib.suppress(Exception):
        correct = bool(scn.validate(final.lower()))

    latencies = list(getattr(agent, "call_latencies", []))
    n_steps = len(latencies)
    category = _categorize(
        correct=correct,
        is_done=is_done,
        n_steps=n_steps,
        run_error=run_error,
        max_rounds=MAX_ROUNDS,
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
        max_repeated_action=_max_repeated_action(action_names),
        failure_category=category,
        final_result=final[:500],
        error=run_error,
        actions=action_names,
        resources=res_stats,
    )


def _fmt_table(results: list[RunResult]) -> str:
    hdr = (
        f"{'scenario':<28} {'t':>1} {'ok':>4} {'steps':>5} {'total_s':>8} "
        f"{'avg_step':>8} {'loop':>4}  {'category':<20}"
    )
    lines = [hdr, "-" * len(hdr)]
    for r in results:
        steps = [s for s in r.per_step_seconds if s > 0]
        avg = round(sum(steps) / len(steps), 1) if steps else 0.0
        ok = "PASS" if r.success else "FAIL"
        lines.append(
            f"{r.scenario_id:<28} {r.tier:>1} {ok:>4} {r.n_steps:>5} "
            f"{r.total_seconds:>8.1f} {avg:>8.1f} {r.max_repeated_action:>4}  "
            f"{r.failure_category:<20}"
        )
    return "\n".join(lines)


def _summary(results: list[RunResult]) -> str:
    n = len(results)
    passed = sum(1 for r in results if r.success)
    cats = Counter(r.failure_category for r in results if not r.success)
    all_steps = [s for r in results for s in r.per_step_seconds if s > 0]
    avg_step = round(sum(all_steps) / len(all_steps), 1) if all_steps else 0.0
    slowest = max((s for s in all_steps), default=0.0)
    lines = [
        "",
        f"PASS {passed}/{n}  ({round(100 * passed / n) if n else 0}%)",
        f"avg step latency: {avg_step}s   slowest single step: {slowest}s",
    ]

    # System-usage peaks across all runs — the hardware-fit half of the decision.
    def _peak(metric: str, key: str = "peak") -> float | None:
        vals = [r.resources.get(metric, {}).get(key) for r in results if r.resources.get(metric)]
        vals = [v for v in vals if v is not None]
        return max(vals) if vals else None

    vram, gpu, cpu, ram = (
        _peak("vram_used_mib"),
        _peak("gpu_util_pct"),
        _peak("cpu_pct"),
        _peak("ram_used_gib"),
    )
    if vram is not None:
        lines.append(
            f"system peaks: VRAM {vram:.0f} MiB / 8192  ·  GPU {gpu:.0f}%  ·  "
            f"CPU {cpu:.0f}%  ·  RAM {ram:.1f} GiB"
        )
    if cats:
        lines.append("failure categories: " + ", ".join(f"{k}={v}" for k, v in cats.most_common()))
    return "\n".join(lines)


async def _main_async(args: argparse.Namespace) -> int:
    if args.goal:
        # Free-form task the user typed. No graded answer, so the validator just
        # passes — you read the final answer yourself. Pair with --headful to watch.
        scenarios = [Scenario(id="custom", tier=0, goal=args.goal, validate=lambda out: True)]
    else:
        scenarios = by_tier(
            max_tier=args.max_tier,
            ids=[s.strip() for s in args.ids.split(",")] if args.ids else None,
        )
    if not scenarios:
        print("no scenarios matched the filter", file=sys.stderr)
        return 2

    try:
        from fara.agents.fara.fara15_agent import Fara15AgentConfig  # noqa: F401
    except Exception as e:  # noqa: BLE001
        print(f"could not import the Fara runtime: {e}", file=sys.stderr)
        print("run this with the Fara venv's python (pip install git+.../microsoft/fara).")
        return 2

    agent_cls = _build_timed_agent_cls()
    from fara.agents.fara.fara15_agent import Fara15AgentConfig as cfg_cls

    print(
        f"model={MODEL}\nbase_url={BASE_URL} runtime=Fara15Agent (native, not browser-use)\n"
        f"max_rounds={MAX_ROUNDS} viewport={VIEWPORT['width']}x{VIEWPORT['height']}\n"
        f"scenarios={len(scenarios)} repeat={args.repeat}\n"
    )

    results: list[RunResult] = []
    for scn in scenarios:
        for attempt in range(1, args.repeat + 1):
            tag = f"[{scn.id} #{attempt}]" if args.repeat > 1 else f"[{scn.id}]"
            print(f"{tag} running…", flush=True)
            r = await _run_one(scn, attempt, agent_cls, cfg_cls)
            results.append(r)
            verdict = "PASS" if r.success else "FAIL"
            print(
                f"{tag} {verdict} steps={r.n_steps} total={r.total_seconds}s "
                f"cat={r.failure_category} [{fmt_stats(r.resources)}] "
                f"answer={r.final_result[:70]!r}",
                flush=True,
            )

    print("\n" + _fmt_table(results))
    print(_summary(results))

    stamp = args.stamp or "fara-latest"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{stamp}.json"
    out_path.write_text(
        json.dumps(
            {
                "model": MODEL,
                "runtime": "fara15-native",
                "base_url": BASE_URL,
                "max_rounds": MAX_ROUNDS,
                "viewport": VIEWPORT,
                "repeat": args.repeat,
                "results": [asdict(r) for r in results],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nwrote {out_path}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Fara-1.5 spike harness (native runtime, Ollama)")
    p.add_argument("--max-tier", type=int, default=None, help="run scenarios up to this tier (1-4)")
    p.add_argument("--ids", type=str, default="", help="comma-separated scenario ids to run")
    p.add_argument("--repeat", type=int, default=1, help="run each scenario N times")
    p.add_argument(
        "--stamp", type=str, default="", help="results filename stamp; default fara-latest"
    )
    p.add_argument("--model", type=str, default="", help="override the Ollama model tag")
    p.add_argument(
        "--base-url", type=str, default="", help="override the OpenAI-compatible base URL"
    )
    p.add_argument(
        "--goal", type=str, default="", help="run ONE free-form task of your own (ungraded)"
    )
    p.add_argument(
        "--headful", action="store_true", help="show the browser window so you can watch"
    )
    args = p.parse_args()

    global MODEL, BASE_URL, HEADLESS
    if args.model:
        MODEL = args.model
    if args.base_url:
        BASE_URL = args.base_url
    if args.headful:
        HEADLESS = False

    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
