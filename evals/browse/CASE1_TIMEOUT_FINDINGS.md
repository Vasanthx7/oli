# Case 1 — "browse timed out after 180s" root-cause finding

**Scenario:** `e3-amazon-add-cable-stop` — *"Add the cheapest USB-C cable to my Amazon
cart, but stop before placing the order."* (profile `amazon`).

**Date:** 2026-09-20. Diagnosed with temporary local timing instrumentation (per-step
`llm_ms/act_ms/shot_ms/step_ms/elapsed_s` in `steps.jsonl` + a `summary.json`, plus a
graceful soft-deadline). That instrumentation is local-only and is removed once the fix
lands — this file is the durable record.

## Symptom

In a live chat, Case 1 returned `browse timed out after 180s and was stopped`, after which
the assistant told the user *"I need a saved Amazon login profile… I don't have one on
file."* The Amazon profile **exists and was authenticated**. The login claim was a
post-timeout **hallucination**, not a real state check.

## Reproduction

```
uv run python -m evals.browse.run_vendored \
  --ids e3-amazon-add-cable-stop \
  --autoroute --allow-manual --save-traces --deadline 0 \
  --stamp case1-diagnosis
```

(`--deadline 0` disables the soft stop so we measure the run's TRUE total time. The
production app has no such disable — it applies the external 180s `tool_timeout_seconds`.)

## Result — the task SUCCEEDS when uncapped

| metric | value |
|---|---|
| outcome | `done` (PASS) — cheapest cable added to cart, stopped before checkout |
| total | **201.9s** over **34 steps** |
| avg step | 5.6s (avg model call **5.2s**; slowest single step 14.9s = the final answer) |
| model tier | **`fara15-4b`** (fast tier — see secondary finding #1) |
| login walls | none — authenticated and shopping throughout (search → price-asc → product → add-to-cart) |
| GPU/VRAM peak | 100% / 6291 MiB of 8192 |

Cumulative elapsed vs. the 180s cap (from `steps.jsonl`):

```
step 29  161.4s
step 30  168.3s
step 31  174.3s      <-- production 180s cap fires here / next step
step 32  180.6s      <-- hard-cancel in prod; no trace tail, no summary
step 33  186.9s
step 34  201.9s  ->  terminate: "Added the cheapest USB-C cable to your cart…"
```

## Root cause

**Wall-clock, not login.** A legitimate multi-step Amazon add-to-cart on the local model
takes ~200s (avg ~5.2s per model call × ~34 steps). The external
`tool_timeout_seconds = 180` (`src/oli/tools/__init__.py::_with_timeout`) hard-cancels the
`browse` coroutine around step 31–32 — **~20s and ~2 steps before it would have
succeeded.** The cancel happens mid-`await`, so nothing records why; the model, seeing only
a generic timeout, invents the login excuse.

## Secondary findings

1. **Autoroute missed the careful tier.** With `--autoroute`, this goal still ran on
   **4B**, not 9B. `_route_model` (`fara_browse.py`) keys off `_INTERACTION_TERMS` /
   `_DENSE_DOMAINS`, but the goal has neither a contiguous `"add to cart"` (it says
   *"cable to my Amazon cart"*) nor `"amazon."` (it says *"amazon cart"*, space not dot).
   Dense-commerce routing silently didn't fire. Note: 9B is *slower per step*, so routing
   there without also raising the time budget would make the timeout **worse**, not better.
2. **Mild stall on the product page.** Steps 24–28 show `stalled: true` — repeat-clicking a
   cluttered product page hunting for "Add to cart" (4B/9B grounding weakness on dense
   pages), adding ~5 steps (~30s) to the run.
3. **The old failure was un-diagnosable by design.** The external cap cancels without a
   trace tail. The new soft-deadline + `summary.json` (`outcome:"deadline"`) turn it into a
   recorded, first-class event with a useful partial answer.

## Recommended fix (separate, committed change)

- **Primary — give browse a realistic time budget.** `browse` is already bounded by
  `MAX_STEPS = 50` (and now the soft-deadline), so the 180s external cap is redundant and
  too tight. Either exempt `browse` from `tool_timeout_seconds` and rely on
  `fara_deadline_seconds` set to a realistic value (~300s), or raise the cap for browse
  specifically. A dense commerce flow legitimately needs ~200s+.
- **Secondary — fix the autoroute heuristic** so "… cart", bare "amazon"/dense-site
  mentions, etc. select the careful tier. Only worthwhile *together with* the larger time
  budget, since 9B is slower per step.
- **Tertiary — no post-failure fabrication.** On timeout/deadline the model must report the
  timeout, not invent a login requirement. Partly handled already by the `personality.md`
  "never fabricate" rule and the graceful partial answer the soft-deadline now returns.

## Cleanup owed (after the fix)

Remove the temporary instrumentation (`started_at`/`step_ms_log`/`llm_ms_log`,
`_write_summary`, per-step timing, the soft-deadline + `fara_deadline_seconds`, and the
`run_vendored` `--save-traces/--deadline` flags — unless we consciously promote the
soft-deadline as a kept feature), delete this diagnosis's `data/fara_traces/*` and
`evals/browse/results/case1-diagnosis.json`. Keep this file.
