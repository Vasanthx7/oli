"""Line up two browse-harness result JSONs side by side (the A/B verdict).

Given the official-harness results (``run_fara.py`` → e.g. ``fara-latest.json``) and the
vendored-loop results (``run_vendored.py`` → e.g. ``vendored-latest.json``), print a
per-scenario PASS/FAIL comparison plus headline deltas (success rate, avg step latency).
This is what decides the ADR 0017 review question: keep the vendored loop, or adopt the
co-designed harness.

    uv run python -m evals.browse.compare fara-latest vendored-latest
    uv run python -m evals.browse.compare results/fara-full.json results/vendored-latest.json

Accepts either a bare stamp (resolved under results/) or a path to a JSON file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RESULTS_DIR = Path(__file__).parent / "results"


def _load(ref: str) -> dict:
    p = Path(ref)
    if not p.exists():
        p = RESULTS_DIR / (ref if ref.endswith(".json") else f"{ref}.json")
    data = json.loads(p.read_text(encoding="utf-8"))
    # Index results by scenario id (first attempt wins for the at-a-glance table).
    by_id: dict[str, dict] = {}
    for r in data.get("results", []):
        by_id.setdefault(r["scenario_id"], r)
    data["_by_id"] = by_id
    data["_label"] = data.get("harness") or p.stem
    return data


def _rate(data: dict) -> tuple[int, int]:
    rs = list(data["_by_id"].values())
    return sum(1 for r in rs if r.get("success")), len(rs)


def _avg_step(data: dict) -> float:
    steps = [s for r in data["_by_id"].values() for s in r.get("per_step_seconds", []) if s > 0]
    return round(sum(steps) / len(steps), 1) if steps else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare two browse-harness result JSONs")
    ap.add_argument("official", help="stamp or path for the official-harness results (run_fara)")
    ap.add_argument("vendored", help="stamp or path for the vendored-loop results (run_vendored)")
    args = ap.parse_args()

    a = _load(args.official)  # official (run_fara)
    b = _load(args.vendored)  # vendored (run_vendored)
    la, lb = f"official:{a['_label']}", f"vendored:{b['_label']}"

    ids = sorted(set(a["_by_id"]) | set(b["_by_id"]))
    hdr = f"{'scenario':<28} {'t':>1}  {la:<22} {lb:<22} {'delta':<10}"
    print(hdr)
    print("-" * len(hdr))
    agree = 0
    for sid in ids:
        ra, rb = a["_by_id"].get(sid), b["_by_id"].get(sid)
        tier = (ra or rb or {}).get("tier", "?")

        def cell(r: dict | None) -> str:
            if not r:
                return "—(absent)"
            ok = "PASS" if r.get("success") else "FAIL"
            return f"{ok} {r.get('total_seconds', 0):>5.1f}s/{r.get('n_steps', 0)}st"

        sa = bool(ra and ra.get("success"))
        sb = bool(rb and rb.get("success"))
        if sa == sb:
            agree += 1
            delta = "same"
        else:
            delta = "OFFICIAL+" if sa else "VENDORED+"
        print(f"{sid:<28} {tier:>1}  {cell(ra):<22} {cell(rb):<22} {delta:<10}")

    pa, na = _rate(a)
    pb, nb = _rate(b)
    n = len(ids)
    print("\n=== headline ===")
    print(f"official ({a['_label']}):  {pa}/{na} pass  ·  avg step {_avg_step(a)}s")
    print(f"vendored ({b['_label']}):  {pb}/{nb} pass  ·  avg step {_avg_step(b)}s")
    print(f"per-scenario agreement: {agree}/{n} ({round(100 * agree / n) if n else 0}%)")
    gap = (pa / na if na else 0) - (pb / nb if nb else 0)
    verdict = (
        "vendored matches official — card divergence is not costing accuracy here"
        if abs(gap) < 1e-9
        else (
            f"official leads by {round(gap * 100)}pp — adopting the harness is justified"
            if gap > 0
            else f"vendored leads by {round(-gap * 100)}pp — keep the vendored loop"
        )
    )
    print(f"verdict: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
