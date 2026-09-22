"""Compare the current LLM classifier vs Jev on the intent decision point.

Runs BOTH backends over a labeled dataset and reports the metrics that drive the
adopt/expand decision: latency (p50/p95/mean), cost ($/1k decisions), accuracy vs gold
per field, cross-agreement (LLM vs Jev), Jev confidence calibration, and error rate —
plus an OVERALL per-1000-turns projection (intent runs on every turn).

Runs the LLM baseline with just a cloud key; the Jev half runs when TYPESAFE_API_KEY is
set (else it's reported as errored, and you still get the baseline). From the repo root:

    uv run python -m evals.decisions.run_compare --stamp jev-intent
    uv run python -m evals.decisions.run_compare --llm-in 0.20 --llm-out 0.20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from statistics import mean

HERE = Path(__file__).parent
DATASET = HERE / "dataset.jsonl"
RESULTS_DIR = HERE / "results"

# Jev pricing: input $42 per billion tokens, output free (see typesafe.ai launch).
JEV_INPUT_USD_PER_TOKEN = 42.0 / 1e9
FIELDS = ("category", "complexity", "needs_tools")


def _pct(xs: list[float], p: float) -> float:
    """Nearest-rank percentile (p in [0,1]); 0.0 for an empty list."""
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, max(0, round(p * (len(s) - 1))))
    return s[i]


def _approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token) — used only for the LLM cost approximation."""
    return max(1, len(text) // 4)


async def _run(args: argparse.Namespace) -> int:
    from langchain_core.messages import HumanMessage, SystemMessage

    from oli import intent, jev

    rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line]
    if args.limit:
        rows = rows[: args.limit]

    clf = intent.get_classifier()
    sys_msg = intent._CLASSIFIER_SYSTEM

    per = {
        "llm": {"lat": [], "errors": 0, "correct": dict.fromkeys(FIELDS, 0), "cost": 0.0, "n": 0},
        "jev": {"lat": [], "errors": 0, "correct": dict.fromkeys(FIELDS, 0), "cost": 0.0, "n": 0},
    }
    agree = dict.fromkeys(FIELDS, 0)
    both_ok = 0
    conf_correct: list[float] = []
    conf_wrong: list[float] = []

    for row in rows:
        q = row["query"]
        gold = {f: row[f] for f in FIELDS}

        # --- LLM baseline (catch errors like _classify_llm, but count them here) ---
        t = time.perf_counter()
        llm_out = None
        try:
            res = await clf.ainvoke([SystemMessage(content=sys_msg), HumanMessage(content=q)])
            llm_out = res if isinstance(res, intent.Intent) else None
        except Exception as e:  # noqa: BLE001
            print(f"  llm error on {q!r}: {type(e).__name__}: {e}")
        per["llm"]["lat"].append(time.perf_counter() - t)
        per["llm"]["n"] += 1
        if llm_out is None:
            per["llm"]["errors"] += 1
        else:
            for f in FIELDS:
                if getattr(llm_out, f) == gold[f]:
                    per["llm"]["correct"][f] += 1
            per["llm"]["cost"] += (
                _approx_tokens(sys_msg + q) / 1e6 * args.llm_in + 20 / 1e6 * args.llm_out
            )

        # --- Jev ---
        prompt = f"{sys_msg}\n\nUser message: {q}"
        t = time.perf_counter()
        jev_out = None
        jev_conf: dict[str, float] = {}
        try:
            jr = await jev.decide(intent.IntentDecision, prompt)
            jev_out, jev_conf = jr.output, jr.confidence
            per["jev"]["cost"] += jr.input_tokens * JEV_INPUT_USD_PER_TOKEN
        except Exception as e:  # noqa: BLE001
            print(f"  jev error on {q!r}: {type(e).__name__}: {e}")
        per["jev"]["lat"].append(time.perf_counter() - t)
        per["jev"]["n"] += 1
        if jev_out is None:
            per["jev"]["errors"] += 1
        else:
            for f in FIELDS:
                if getattr(jev_out, f) == gold[f]:
                    per["jev"]["correct"][f] += 1

        # --- cross-agreement + calibration (rows where both succeeded) ---
        if llm_out is not None and jev_out is not None:
            both_ok += 1
            for f in FIELDS:
                if getattr(llm_out, f) == getattr(jev_out, f):
                    agree[f] += 1
            if jev_conf:
                c = min(jev_conf.values())
                (conf_correct if jev_out.category == gold["category"] else conf_wrong).append(c)

    _report(per, agree, both_ok, conf_correct, conf_wrong, len(rows), args)
    return 0


def _report(per, agree, both_ok, conf_correct, conf_wrong, n, args) -> None:
    def line(b: str) -> str:
        d = per[b]
        ok = d["n"] - d["errors"]
        acc = {f: (d["correct"][f] / ok * 100 if ok else 0.0) for f in FIELDS}
        cost_1k = (d["cost"] / ok * 1000) if ok else 0.0
        return (
            f"{b:<4} {_pct(d['lat'], 0.5) * 1000:>7.0f} {_pct(d['lat'], 0.95) * 1000:>7.0f} "
            f"{mean(d['lat']) * 1000 if d['lat'] else 0:>7.0f}  ${cost_1k:>8.4f}  "
            f"{acc['category']:>5.0f} {acc['complexity']:>6.0f} {acc['needs_tools']:>6.0f}  "
            f"{d['errors'] / d['n'] * 100 if d['n'] else 0:>5.0f}"
        )

    print(f"\n=== intent decision: LLM vs Jev over {n} labeled queries ===")
    hdr = (
        f"{'be':<4} {'p50ms':>7} {'p95ms':>7} {'meanms':>7}  {'$/1k':>9}  "
        f"{'cat%':>5} {'cplx%':>6} {'tools%':>6}  {'err%':>5}"
    )
    print(hdr)
    print("-" * len(hdr))
    print(line("llm"))
    print(line("jev"))

    if both_ok:
        print(f"\nagreement (LLM vs Jev, {both_ok} rows where both ran):")
        for f in FIELDS:
            print(f"  {f:<12} {agree[f] / both_ok * 100:>5.0f}%")
    if conf_correct or conf_wrong:
        cc = mean(conf_correct) if conf_correct else 0.0
        cw = mean(conf_wrong) if conf_wrong else 0.0
        print(f"\nJev calibration: mean confidence  correct={cc:.2f}  wrong={cw:.2f}")

    # Overall projection: intent runs on every turn, so per-turn deltas scale by volume.
    lm, jm = (
        mean(per["llm"]["lat"]) if per["llm"]["lat"] else 0,
        (mean(per["jev"]["lat"]) if per["jev"]["lat"] else 0),
    )
    ok_l, ok_j = per["llm"]["n"] - per["llm"]["errors"], per["jev"]["n"] - per["jev"]["errors"]
    cl = (per["llm"]["cost"] / ok_l * 1000) if ok_l else 0.0
    cj = (per["jev"]["cost"] / ok_j * 1000) if ok_j else 0.0
    print("\noverall (moving intent LLM -> Jev), per 1000 turns:")
    print(
        f"  latency saved: {(lm - jm) * 1000:.0f} ms/turn  ->  {(lm - jm) * 1000:.1f} s / 1k turns"
    )
    print(f"  cost delta:    ${cl - cj:.4f} / 1k turns  (llm ${cl:.4f} vs jev ${cj:.4f})")
    if per["jev"]["errors"] == per["jev"]["n"]:
        print("  NOTE: Jev errored on every row (no key / dep missing) — LLM baseline only.")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{args.stamp or 'jev-intent'}.json"
    out.write_text(json.dumps({"n": n, "per": per, "agree": agree}, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")


def main() -> int:
    p = argparse.ArgumentParser(description="LLM vs Jev on the intent decision point")
    p.add_argument("--stamp", default="", help="results filename stamp (default jev-intent)")
    p.add_argument("--limit", type=int, default=0, help="only the first N rows")
    p.add_argument("--llm-in", type=float, default=0.20, help="LLM input $/1M tokens (adjust)")
    p.add_argument("--llm-out", type=float, default=0.20, help="LLM output $/1M tokens (adjust)")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
