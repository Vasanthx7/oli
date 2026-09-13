> **Findings drop.** Curated finding + results from the initial Fara-1.5-4B spike,
> brought to `dev` for visibility. The harness lives on `chore/fara-spike`. See
> [BENCHMARK.md](BENCHMARK.md) for the full multi-model comparison.

# Fara-1.5-4B spike — results

**Date:** 2026-09-12 · **Hardware:** RTX 3060 8 GB · **Branch:** `chore/fara-spike`

## The one question

Desk research couldn't answer this: **does a quantized Fara-1.5-4B, run through its
own native runtime, beat our current local browse setup (qwen2.5vl:7b via
browser-use) on our hardware — without being too slow?**

Answer from this spike: **yes, decisively — and it's faster, not slower.**

## Setup

- **Model:** `bartowski/Fara1.5-4B-GGUF:Q4_K_M` (2.88 GB) + `mmproj-Fara1.5-4B-f16`
  (672 MB vision projector). Served by Ollama as the local tag `fara15-4b`.
  - `ollama pull hf.co/...` repeatedly hit `context deadline exceeded` on HF's
    *manifest* fetch even though both blobs downloaded to 100%. Worked around by
    building locally from the cached blobs with a two-`FROM` Modelfile
    (model + mmproj). Same weights, local tag. See `_make_model_notes` below.
- **Runtime:** Microsoft's own `Fara15Agent` + its `PlaywrightEnvironment`
  (`microsoft/fara`), i.e. the native observe-think-act **coordinate loop — NOT
  browser-use**. Pointed at Ollama's OpenAI-compatible endpoint
  (`http://localhost:11434/v1`); the loop's only non-default create-arg is
  `temperature: 0`, so nothing vLLM-specific is required. Installed in an isolated
  venv so it never touches oli's locked deps.
- **Harness:** `evals/browse/run_fara.py`. Reuses the *same* graded scenarios and
  validators as `run.py` (so the comparison is honest) and the same metric
  families. `max_rounds=12` matches production browse's `MAX_STEPS`. Per-step model
  latency is measured by timing each `chat.completions` call directly (Fara's loop
  doesn't emit browser-use's `StepMetadata`).

## Results — Fara-1.5-4B (Q4_K_M), native runtime, all tiers

```
scenario                     t   ok steps  total_s avg_step loop  category
t1-example-heading           1 PASS     3     22.8      2.4    1  ok
t1-httpbin-html-title        1 PASS     2     15.4      2.2    1  ok
t2-hn-top3                   2 PASS     3     23.7      2.5    1  ok
t2-wikipedia-python-year     2 PASS     3     23.2      2.4    1  ok
t3-ddg-search-firstresult    3 FAIL    12    130.1      3.5    9  wrong-answer
t3-wikipedia-search          3 PASS     6     55.8      3.3    1  ok
t4-hn-newest-firstpoint      4 PASS    12    136.8      3.8    2  ok
t4-books-toscrape-price      4 PASS     4     36.7      2.9    1  ok

PASS 7/8 (88%)   avg step latency: 3.2s   slowest single step: 4.66s
```

## Comparison vs the current setup (qwen2.5vl:7b via browser-use)

Baseline runs are from earlier the same day, **tiers 1–2 only** (the qwen harness
was never run past tier 2). Direct, same-scenario comparison on tiers 1–2:

| metric                    | Fara-1.5-4B (native)   | qwen2.5vl:7b (browser-use) |
|---------------------------|------------------------|----------------------------|
| pass rate, tiers 1–2      | **4/4 (100%)**         | 2/4–3/4 (50–75%), flaky    |
| avg model latency / step  | **~2.4 s**             | ~17–20 s (7 s in flash)    |
| steps per scenario        | **2–3**                | 3–14 (often loops to 14)   |
| wall-clock per scenario   | **~15–24 s**           | ~25–330 s                  |
| trivial-task loops        | none (loop=1)          | t1 looped to 14 steps/199s |

Net: **~6–7× faster per step**, far fewer steps, and **~7–10× faster wall-clock**,
while being *more* reliable. And Fara cleared tiers 3–4 (multi-hop, form-fill,
e-commerce) that the qwen baseline never even attempted.

## Honest caveats

- **t3-ddg-search FAIL:** Fara looped 9× on DuckDuckGo's HTML search box and ran out
  of rounds. Real weakness on that specific form interaction — worth a second look.
- **t4-hn-newest is a soft pass:** that validator only checks for *any digit*, and
  Fara's final text was an action echo ("I clicked at coordinates (506.88, 52.2)")
  after hitting the 12-round ceiling. Counts as pass but isn't a clean success.
- **Latency is warm.** The very first call after model load is ~35 s (VRAM load);
  every subsequent step is the ~2–5 s reported above. Cold start is a one-time cost.
- Single run (`repeat=1`); no flakiness sweep yet. Baseline is tiers 1–2 only.

## Reproduce

```bash
# 1. Build the model on Ollama (Modelfile FROM both cached blobs; see below)
ollama create fara15-4b -f Modelfile

# 2. Isolated venv with the native runtime
uv venv .venv-fara --python 3.11
VIRTUAL_ENV=.venv-fara uv pip install "git+https://github.com/microsoft/fara.git"
.venv-fara/Scripts/python -m playwright install chromium   # playwright==1.51 build

# 3. Run (same scenarios/validators as run.py)
.venv-fara/Scripts/python -X utf8 -m evals.browse.run_fara --stamp fara-full
```

Modelfile (paths point at the already-downloaded Ollama blobs):

```
FROM <blobs>/sha256-aedd3e6ed70...   # Fara1.5-4B-Q4_K_M.gguf   (2.88 GB)
FROM <blobs>/sha256-373320845fb5...  # mmproj-Fara1.5-4B-f16    (672 MB)
PARAMETER temperature 0
PARAMETER num_ctx 16384
```

## Recommendation

Strong candidate to replace qwen2.5vl:7b as the local browse model. Before
committing: (1) fix/understand the DDG-style search-box loop, (2) tighten the
loose validators and re-run tiers 3–4, (3) run `--repeat 3` for a flakiness read,
and (4) decide whether to adopt Fara's native loop or port its prompt/action
schema onto our browser-use pipeline (the native loop is what made it fast and
clean here, so leaning toward adopting it).
