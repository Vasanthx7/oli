> **Findings drop.** This is the curated finding + test results from the Fara browse
> benchmark spike, brought to `dev` for visibility. The eval harness that produced
> these numbers (`run_fara.py`, `run_cua.py`, `scenarios.py`, `_resources.py`) and
> the raw per-run JSON live on the `chore/fara-spike` branch — only the finding and
> results are included here by request.

# Local browse model benchmark — decision report

**Date:** 2026-09-12 · **Hardware:** RTX 3060 Ti, 8 GB VRAM (Windows) · **Branch:** `chore/fara-spike`

Goal: pick the local model for autonomous `browse` on this 8 GB box, on numbers —
accuracy **and** hardware fit — not vibes. All models run the **same 10 graded
scenarios** ([`scenarios.py`](scenarios.py), tiers 1–4) with strict validators, and
the **same GPU/CPU/RAM sampler** ([`_resources.py`](_resources.py)).

## TL;DR

**Adopt Fara-1.5-4B (Q4_K_M).** It's the only option that is accurate, fast, *and*
fits with headroom. Fara-9B is the accuracy ceiling but maxes the card and runs ~3×
slower; UI-TARS-1.5-7B doesn't fit (spills to CPU) and underperforms on web
read/extract; qwen2.5vl:7b (current) is the slowest and flakiest.

## Results

| model | runtime | pass | latency/step | VRAM peak (of 8192) | CPU peak | verdict |
|---|---|---|---|---|---|---|
| **Fara-1.5-4B** Q4 | native (official) | **24/30 = 80%** (3× each) | **2.8 s** | **6108 MiB — fits, ~2 GB free** | 67% | ✅ **pick** |
| Fara-1.5-9B Q4 | native (official) | 9/10 = 90% (1×) | 8.7 s | 7925 MiB — maxed | 93% | ceiling, too heavy |
| **UI-TARS-1.5-7B q3** (16k ctx) | generic loop | **8/10 = 80%** (1×) | ~6.2 s (typ. 3.3 s) | 7483 MiB — fits | 29% | close alternative |
| UI-TARS-1.5-7B Q4 (32k ctx) | generic loop | ~5/10 (1×) | ~12.2 s | 8.0 GB — **spills 26% to CPU** | 87% | ❌ config didn't fit |
| qwen2.5vl:7b | browser-use | ~50–75% (tiers 1–2) | 17–20 s | — | — | current; slowest/flakiest |

**Quantization/context matters — UI-TARS was badly under-rated at first.** The initial
UI-TARS run used Q4_K_M at the model's default 32 k context, which loads at 8.0 GB and
**spills 26% to CPU** (`ollama ps` = `26%/74% CPU/GPU`) → ~12 s/step, and the slowness
caused loops/timeouts (5/10). Re-built at **q3_k_m with a 16 k context it fits fully in
VRAM** (7.5 GB, 100 % GPU, ~3.3 s/step typical) and jumps to **8/10** — tying Fara-4B on
accuracy. Lesson: on an 8 GB card, "does the quant+context fit VRAM" dominates raw model
quality. (An 8 k context was too small — 5 screenshots overflow it; 16 k is the sweet spot.)

## Per-scenario (Fara-4B ×3 vs Fara-9B vs UI-TARS)

(UI-TARS column = the fair **q3/16k** run that fits VRAM.)

| scenario | tier | Fara-4B | Fara-9B | UI-TARS-7B q3 |
|---|---|---|---|---|
| t1-example-heading | 1 | ✅ 3/3 | ✅ | ✅ (1 step) |
| t1-httpbin-html-title (read body) | 1 | ✅ 3/3 | ✅ | ✅ |
| t2-wikipedia-python-year (1991) | 2 | ✅ 3/3 | ✅ | ✅ |
| t2-books-catalogue-count (1000) | 2 | ✅ 3/3 | ✅ | ✅ |
| t2-quotes-first-author (Einstein) | 2 | ✅ 3/3 | ✅ | ❌ loops |
| t3-ddg-search-firstresult | 3 | ❌ 0/3 loop×9 | ✅ **(9B fixes it)** | ❌ loops |
| t3-wikipedia-search (Ada Lovelace) | 3 | ✅ 3/3 | ✅ | ✅ |
| t3-httpbin-form-submit | 3 | ❌ 0/3 loop×5 | ❌ | ✅ **(UI-TARS fixes it)** |
| t4-books-home-firstprice (£51.77) | 4 | ✅ 3/3 | ✅ | ✅ (1 step) |
| t4-books-travel-multihop | 4 | ✅ 3/3 | ✅ | ✅ |

Note the **complementary failure modes**: Fara-4B nails read/extract but fails both
form-fill cases (ddg search box, httpbin form); UI-TARS-q3 handles the httpbin **form**
that Fara can't, but fails a plain extract (quotes) and the ddg search box. Only Fara-9B
cracks the ddg search box — no model here does all three interaction cases.

## What the numbers say

- **Fara-4B is the sweet spot.** 8/10 scenarios pass 3/3 with near-zero variance and
  ~2.2–3.5 s/step, at 6.1 GB VRAM — leaving room for the FastAPI app and the other
  local models the assistant uses (qwen2.5:7b-instruct, embeddings). The two failures
  are a *specific* gap (below), not general weakness.
- **Fara-9B buys one scenario (DDG search) for a steep price:** ~3× slower/step and
  VRAM+CPU pinned at the ceiling, so it would starve everything else on the box. The
  form-fill failure persists at 9B — so that gap is training/prompt, not capacity.
- **UI-TARS-1.5-7B (q3, fitted) is a genuine alternative — and the best at forms.**
  Once quantized/contexted to fit VRAM it scores 8/10 at ~3.3 s/step typical, ties
  Fara-4B on accuracy, and is the *only* small model that completes the httpbin form.
  It's weaker than Fara-4B on the margins that matter here: ~2× slower per step, less
  VRAM headroom (7.5 vs 6.1 GB), a more aggressive quant (q3 vs q4 → higher quality
  risk), it runs in our generic loop rather than an official one, and it was tested 1×
  vs Fara-4B's 3×. Strong second choice, especially if form-filling becomes central.
- **Common gap across every local model: form-fill + submit** (httpbin form; DDG
  search box). Only 9B cracked the DDG search box. Worth a targeted prompt/loop fix
  regardless of which model we ship.

## Fairness & caveats (read before quoting these)

- **Runtimes differ by necessity.** Fara runs in its *own* official loop (the fairest
  "Fara at its best", and how we'd deploy it). UI-TARS runs in a neutral generic loop
  ([`run_cua.py`](run_cua.py)) with its real prompt + action parser; its click
  coordinate convention was verified with a smoke test before scoring. Both are "each
  model at its best."
- **Navigation affordance.** UI-TARS navigates via a browser address bar that a
  headless page-only Playwright session doesn't render, so we **seed the start URL**
  (standard for UI-TARS web evals). Fara navigates itself via its `visit_url` action.
- **Repeat counts differ.** Fara-4B was run **3×** each (the candidate — most robust
  number); Fara-9B and UI-TARS **1×** each. Fara-4B's number is therefore the most
  trustworthy; the 1× runs could move ±1 on a re-run.
- **UI-TARS headline = the q3/16k run** (fits VRAM, all 10 scenarios). The earlier
  Q4/32k run (5/10) is kept only as the cautionary "didn't fit" data point.
- **qwen baseline is tiers 1–2 only** (the older runs never went past tier 2).
- Single hardware, single day, local network.

## Not tested: ScaleCUA-3B (and why)

We wanted ScaleCUA-3B (OpenGVLab, a Qwen2.5-VL-3B computer-use agent) in the mix, but
**couldn't test it on this stack without a disproportionate detour**:

- **No GGUF exists.** OpenGVLab ships only original `safetensors` (3B/7B/32B); there is
  no GGUF conversion on Hugging Face and nothing on the Ollama registry (the apparent
  "scalecua" search hits were just the query echoed back). Ollama serves GGUF only.
- **Testing it would require a full self-conversion:** download ~7 GB of fp16 weights →
  run llama.cpp's `convert_hf_to_gguf` for the text tower **and** extract/convert the
  Qwen2.5-VL vision tower to an `mmproj` → quantize → build an Ollama model. Vision-tower
  conversion for a *custom* checkpoint is the fragile step (config quirks, mmproj op
  support) and can silently produce a model that "loads" but grounds coordinates wrong.
- **Plus a new adapter:** ScaleCUA emits Python-call actions wrapped in `<action>…</action>`
  with its own pixel constraints (`min_pixels=3136`, `max_pixels=2109744`, different from
  UI-TARS), so it needs its own parser + coordinate mapping + a coord smoke-test.

That's ~30–60 min of conversion work with real risk of an unfair/broken result, for a
model in the same Qwen2.5-VL-3B family we've already characterized. Deferred by decision —
revisit if OpenGVLab (or the community) publishes a vetted GGUF + mmproj.

## Recommendation

1. **Ship Fara-1.5-4B** as the local browse model (replacing qwen2.5vl:7b).
2. **Integrate its native loop** into `oli.tools.browse` + the live browser view
   (that loop is what makes it fast/clean — don't just drop weights into browser-use).
3. **Fix the form-fill gap** (prompt/loop tweak for search boxes & form submits) — the
   one capability every local model here was weak on.
4. Keep Fara-9B on the shelf as the "accuracy mode" if we ever move to a ≥12 GB card.

Raw per-run JSON + trace screenshots are under `results/` (git-ignored). Reproduce
with `run_fara.py` (Fara) and `run_cua.py --adapter uitars` (rivals); see
[`FARA_SPIKE.md`](FARA_SPIKE.md) for the model-build details.
