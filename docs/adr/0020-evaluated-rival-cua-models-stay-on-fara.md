# 20. Evaluated rival computer-use models (NVIDIA VLMs, Holo-3.1) — stay on Fara-1.5

Date: 2026-10-04

## Status

Accepted

## Context

Fara-1.5 was chosen as the `browse` computer-use engine in [ADR 0016](0016-native-fara-browse-engine.md).
Since then two prompts to reconsider arrived: (1) NVIDIA shipped a wave of open
vision models, and (2) the late-2026 open-weight computer-use field has moved
(UI-TARS, UI-Venus, Qwen-UI-Agent, Holo). We asked whether anything lighter/faster
could beat Fara on **our** hardware (8 GB GPU) and **our** scenarios, with a
commercial-friendly license (Oli is deployed).

Two layers of evaluation:

- **Desk research — NVIDIA VLMs.** The headline releases (Nemotron Nano 2 VL ~12B,
  Nemotron 3 Nano Omni, Nemotron 3.5 Lightning) are **perception/document-intelligence
  VLMs, not computer-use agents** — they read documents/video, they don't drive a
  GUI. Not drop-in for `browse`. The one genuinely relevant piece, **LocateAnything-3B**
  (fast GUI grounding, ScreenSpot-Pro), is **NVIDIA-license non-commercial** — a
  blocker for a deployed product. Net: nothing from NVIDIA to adopt for `browse`.
- **Live A/B — Holo-3.1 vs Fara.** Holo-3.1 (H Company) was the strongest
  *clean-license* challenger from the survey: **Apache-2.0, Qwen-3.5-based** (same
  base family as Fara), 0.8B/4B/9B/35B-A3B, ships **Q4 GGUF** for local inference.
  We built an eval-only Holo loop (`evals/browse/holo_browse.py` + `run_holo.py`) that
  speaks Holo's native navigation contract (vendored from `hcompai/hai-cookbook`:
  single JSON `{note, thought, action}`, **absolute-pixel** coordinates, stateless
  per-step prompt with a running note-memory) and emits the **same `RunResult`** as
  the Fara harness, so `compare.py` lines them up 1:1. Everything else — viewport,
  step budget (`MAX_STEPS`), scenarios, validators — is held constant so the A/B
  isolates the model.

## Decision

**Stay on Fara-1.5 as the `browse` engine.** Keep the Holo harness as an eval-only
tool for future rematches; do not add Holo (or any NVIDIA VLM) to production.

## A/B result (2026-10-04, `ahmadwaqar/holo-3.1` Q4 GGUF vs `fara15-4b`, tiers 1–4)

| | Fara-1.5 4B | Holo-3.1 |
|---|---|---|
| Pass rate | **9/10** | **7/10** |
| Avg step latency | 3.9 s | 5.3 s |
| Per-scenario agreement | — | 80 % |

- **Reads/nav/extract (tiers 1–2 + single-fact tier-4): tie.** Both answer the same
  facts; Holo is ~2–3× slower per step on the quick ones.
- **Interaction + multi-hop: Fara wins.** Holo **loops** — max-repeat 13–15 on
  `t3-ddg-search`, **49** on `t4-books-travel-multihop` — burning the whole step
  budget → `incomplete-max-rounds`. Run-to-run variance too (an earlier pass scored
  Holo 8/10); those two scenarios are flaky for it.
- **Shared fail is a stale validator, not a model gap:** `t1-example-heading` —
  example.org changed its copy, so both models read the body, not the `<h1>`.

Two honest caveats on the margin: (1) the loop gap is **partly scaffolding** — the
Holo adapter is deliberately minimal and has none of the Fara loop's stall-detection
nudges (which keep Fara's loop counts at 1–3); a fair rematch would port those in.
(2) The A/B surfaced and fixed one real adapter bug — Holo occasionally emits a
coordinate as a string (`"137, "`) that crashed `int()`, now handled by a tolerant
parser. Even allowing for both, Holo would need to close a two-scenario + latency gap.

## Consequences

- **Good:** decision is now numbers-backed, not vibes; Fara stays the engine (fast,
  loops rarely, clean MIT license, fits the 8 GB card). A reusable **rival-model
  harness** now exists (`run_holo` + `compare`) so the next challenger is a one-day
  bake-off, not a rebuild.
- **Cost/risk:** the Holo adapter is eval-only and unmaintained against upstream
  drift; treat its numbers as a snapshot. Result JSONs are git-ignored (convention) —
  this ADR + the `evals/browse/README.md` outcome line are the durable record.
- **Follow-ups / rematch triggers:** re-run if (a) we move to a ≥12 GB card (test
  Holo-9B / Fara-9B head-to-head), (b) UI-Venus-2 relicenses to true Apache-2.0 (it
  beats Fara on published web benchmarks but its current license is research-only),
  or (c) we port Fara's anti-stall nudges into the Holo adapter for an apples-to-apples
  rematch. Also fix the stale `t1-example-heading` validator regardless of engine.
