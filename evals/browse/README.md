# Browse evaluation harness

Systematically measures the **local Fara computer-use models** — where they fail, how
fast they are, and how robust they are — so we drive speed and reliability with numbers
instead of anecdotes. Computer-use is local, vision-only, and the only local workload
(see [ADR 0016](../../docs/adr/0016-native-fara-browse-engine.md) /
[ADR 0017](../../docs/adr/0017-cloud-provider-failover-local-vision-only.md)).

Two harnesses run the **same** graded scenarios/validators against the same Ollama model,
so their results are apples-to-apples (see the A/B section below):

- `run_fara.py` — Microsoft's **official** Fara-1.5 harness (its coordinate loop +
  Playwright env), run in an isolated Fara venv.
- `run_vendored.py` — oli's **production** loop (`oli.tools.fara_browse`), run in the
  oli venv.

(The earlier `qwen2.5vl:7b` browser-use baseline runner was retired with the browser-use
engine — ADR 0017.)

## What it measures (per run)

| Family | Fields |
|--------|--------|
| Success | `success` (finished **and** validator-correct), `correct`, `is_done` |
| Latency | `total_seconds`, `per_step_seconds` (per model-call latency) |
| Steps / loops | `n_steps`, `max_repeated_action` (longest run of the identical action — the loop fingerprint) |
| Failure | `failure_category`: `oom` · `parse-error` · `timeout` · `connection` · `navigation` · `incomplete-max-rounds` · `wrong-answer` · `incomplete` · `error` · `ok` |

## Scenarios

Defined in [`scenarios.py`](scenarios.py), graded by tier:

- **Tier 1** — navigate + read one obvious fact (baseline wiring)
- **Tier 2** — extract several items / a derived fact (reading comprehension)
- **Tier 3** — interact: search box, follow a result (form fill + click)
- **Tier 4** — dynamic / multi-hop / JS-heavy (the stress cases)

Edit `scenarios.py` to change what "the model can do" means. Each scenario has a
`validate(final_result)` that grades the answer independently of the agent's own
self-reported success.

## Fara-1.5 spike (background)

See [`FARA_SPIKE.md`](FARA_SPIKE.md) and [`BENCHMARK.md`](BENCHMARK.md) for the original
spike that chose Fara-1.5 (native runtime, quantized 4B on Ollama) — the decision behind
ADR 0016.

## A/B: vendored loop vs the co-designed harness

The Fara model card says to run the model **only** with its co-designed harness
(fara CLI / MagenticLite), yet oli ships a *vendored reimplementation* of that loop
(`oli.tools.fara_browse`, ADR 0016) to avoid a Playwright pin conflict and to keep the
live-view/profile integration. ADR 0017's review asks: **does our loop actually match
the official one on our hardware?** These two runners answer it on identical scenarios,
the same Ollama model, and the same result schema:

- `run_fara.py` — Microsoft's **official** harness (`fara.agents.Fara15Agent`), run in
  an isolated Fara venv (it pins `playwright==1.51`, which conflicts with oli's).
- `run_vendored.py` — oli's **production** loop (`oli.tools.fara_browse`), run in the
  oli venv. It monkeypatches only the OpenAI client to recover per-step latency + the
  action fingerprint; the loop itself is untouched.

```bash
# 1) official harness (in the FARA venv, Ollama serving the model)
<fara-venv>/Scripts/python -m evals.browse.run_fara     --max-tier 4 --stamp fara-ab
# 2) vendored loop (in the OLI venv, same Ollama + model)
uv run python -m evals.browse.run_vendored              --max-tier 4 --stamp vendored-ab
# 3) line them up + get the verdict
uv run python -m evals.browse.compare fara-ab vendored-ab
```

`compare.py` prints a per-scenario PASS/FAIL table, success-rate + latency deltas, and a
verdict (match → keep vendored; official leads → adopting the harness is justified). Run
both at the same step budget for the fairest fight — note `run_fara` defaults to
`MAX_ROUNDS=12` while the vendored loop uses its production `MAX_STEPS`; pass matching
values if you want them identical.

## Running

Needs a reachable Ollama serving the Fara model, plus a Chromium (`playwright install
chromium`). From the repo root, with the venv active:

```bash
uv run python -m evals.browse.run_vendored --max-tier 2         # smoke: tiers 1-2
uv run python -m evals.browse.run_vendored                      # all tiers
uv run python -m evals.browse.run_vendored --ids t3-ddg-search-firstresult
uv run python -m evals.browse.run_vendored --repeat 3 --stamp flaky   # flakiness: 3x each
uv run python -m evals.browse.run_vendored --base-url http://192.168.1.50:11434/v1
```

Results print as a table + summary and are written to
`evals/browse/results/<stamp>.json` (default stamp `vendored-latest`). The JSON files
are git-ignored; commit a summary or a curated snapshot if you want to track trends.

## Reading the output

- **`loop` column high** (≥3) → the model is re-issuing the same action; it isn't
  registering that the last step made no progress. A hardness problem.
- **`avg_step` high** → per-step latency; the speed lever. Compare tiers — vision
  steps with a screenshot are the expensive ones.
- **`incomplete-max-steps`** → ran out of the step budget (`browse.MAX_STEPS`)
  without finishing; usually loops or dithering.
- **`wrong-answer`** → it *thought* it was done but the fact was wrong; a
  comprehension/grounding problem, not a mechanics problem.
