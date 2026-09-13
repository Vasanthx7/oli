# 16. Native Fara-1.5 browse engine (local, no cloud fallback)

Date: 2026-09-13

## Status

Accepted

## Context

`browse` has run on **browser-use** driving a strong cloud model (Groq) — a
DOM+screenshot agent (ADR 0010/0013). A benchmark of local computer-use models on
our 8 GB RTX 3060 Ti (`evals/browse/BENCHMARK.md`) found **Fara-1.5-4B**, run in
its *own* vision-only observe-think-act loop, clearly best for our hardware:
~80% on 10 graded scenarios at ~2.8 s/step and ~6 GB VRAM, versus browser-use +
qwen2.5vl:7b (slow, flaky) and heavier rivals (Fara-9B maxes the card; UI-TARS
only competes once quantized to fit). The wins come from the *native loop*
(direct pixel-coordinate grounding, small prompts, trained termination), not just
the weights — so the integration must reproduce that loop, per the model card's
guidance to use it only with its own harness (MagenticLite).

Three forces shape the design:

1. **Dependency conflict.** The `fara` package pins `playwright==1.51`, which
   clashes with the Playwright browser-use needs. We cannot add it to oli's env.
2. **Deployment.** The model runs on the developer's local GPU box. The deploy VM
   has no GPU and reaches that box's Ollama **over Tailscale**. There is no
   always-on cloud option we want to fall back to — Groq is rate-limited and
   we'd rather fail loudly than silently burn quota.
3. **The live browser view** (ADR 0011: watch + take-control) is wired to
   browser-use's `Agent`/CDP; Fara's loop has neither a CDP URL nor pause/resume.

## Decision

Add a **vendored, self-contained Fara browse engine** (`oli.tools.fara_browse`),
selected by `settings.browse_engine == "fara"`:

- **No `fara` dependency.** We reproduce the loop with oli's own Playwright +
  `openai` client, and vendor the model's **verbatim** system prompt
  (`fara_system_prompt.txt`, extracted from the model). Operating point matches
  the card: **1440×900, temperature 0, most-recent-3 screenshots**, coordinates in
  a 0–1000 space scaled to viewport pixels.
- **No cloud fallback.** If the Ollama host is unreachable, return a clear
  "unavailable" message (optionally a `FARA_UNAVAILABLE_URL` walkthrough link).
- **Watch + take-control preserved.** `live_browser.attach_page(page, agent)`
  screencasts the page we own; take-control toggles an `asyncio` gate the loop
  checks at each step boundary; input is scaled to the page's real 1440×900.
- **Two-tier auto-routing.** `browse` picks the model per task: clean read / nav /
  extract → **Fara-4B** (fast, fits with headroom); interaction-heavy or dense-page
  goals (add-to-cart, forms, checkout, or commerce/bot-heavy domains) → **Fara-9B**
  (`fara_model_heavy`), which grounds small controls on cluttered pages reliably
  where 4B lands *near* not *on* them (verified: 9B completed the Amazon add-to-cart
  flow, self-confirmed cart 3→4; 4B could not). Keyword heuristic over the goal;
  toggle with `fara_autoroute`.
- **Profiles** work via a persistent context, as before.
- **Config over Tailscale.** `FARA_BASE_URL` points at the GPU box; in production
  that's its Tailscale IP/MagicDNS name, and the host needs `OLLAMA_HOST=0.0.0.0`
  so Ollama accepts connections beyond localhost. Default engine stays
  `browser-use`, so this is opt-in and non-breaking.

## Consequences

- **Good:** fast, cheap, private local browsing that fits our hardware; the live
  view keeps working; no dependency conflict; failure is explicit, not silent.
- **Cost/risk:** we maintain a small reimplementation of Fara's loop (drift risk
  vs the official harness — mitigated by vendoring the exact prompt and matching
  the operating point). Browse is unavailable when the GPU box is offline (by
  design). Take-control pauses at step boundaries, not mid-action.
- **Form-fill/search-box weakness — mitigated.** The engine now detects stalls (the
  same action type three steps running) and injects an **action-aware nudge** (a
  repeated click on a field → "it's focused, type now"; repeated scrolling → "stop,
  click the target"), plus a short form/search hint on the task and a screenshot
  retry through navigation. This eliminated the pathological loops (10× click on a
  search box, 6× scroll on a form) with **no regression** (8/10, the 8 non-form
  scenarios still pass, fast). Residual: DuckDuckGo's HTML endpoint bot-blocks the
  headless browser — the agent now fails *gracefully* (reports the block) instead of
  looping; and httpbin's multi-field form (radios/checkboxes/textarea) is still
  beyond reliable 4B grounding within budget (9B also fails it). A larger model is
  the lever for the hardest forms.
- **Follow-ups:** revisit a bigger model on a ≥12 GB card (Fara-9B was the accuracy
  ceiling); add the recorded-workflow redirect; consider surfacing engine health in
  the UI.
