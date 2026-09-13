# Fara browse: vendored loop vs. the official harness — A/B result + takeaways

**Question (from the ADR 0017 review):** the Fara-1.5 card says to run the model *only*
with its co-designed harness (fara CLI / MagenticLite), yet oli ships a vendored
reimplementation of that loop (`oli.tools.fara_browse`, ADR 0016). Does that divergence
cost us anything on our hardware?

**Run:** 2026-09-13, RTX 3060 (8 GB), Ollama serving `fara15-4b`, the 10 graded scenarios
in `scenarios.py`. `run_vendored.py` drives our production loop; `run_fara.py` drives
Microsoft's official `fara.agents.Fara15Agent` (installed in an isolated venv). Same
scenarios, same model, same result schema — diffed with `compare.py`.

## Result: they match — keep the vendored loop

| metric | official (fara-cli) | vendored (oli) |
|---|---|---|
| pass rate | **8/10** | **8/10** |
| per-scenario agreement | — | **10/10 (100%)** |
| avg model-step latency | 3.2 s | 3.4 s |
| peak VRAM | ~5.8 GB | ~6.6 GB |

Both fail the *same two* scenarios — `t3-ddg-search-firstresult` (DuckDuckGo bot-blocks
the headless browser) and `t3-httpbin-form-submit` (multi-field form grounding beyond 4B)
— i.e. **model/site limits, not harness differences**. The lever for those is a bigger
model (9B/27B) or a non-blocking search source, not the harness.

**Verdict:** the card's "use only our harness" guidance is not costing us accuracy here.
The vendored loop reproduces the official results exactly, runs faster, and keeps our
live-view + profiles integration. No reason to swap the browse engine to fara-cli.

## Why our harness is faster (often 2–3× wall-clock)

Per-scenario wall-clock was much lower for us (e.g. t1 **7.9 s** vs 30.7 s; t3-wiki
**23 s** vs 67.6 s) *even though model-step latency is ~equal*. The difference is **per-step
browser/environment I/O**, not the model:

| per step | official harness | vendored loop |
|---|---|---|
| screenshots | **pre + post** (2), saved to disk | **1** (in-memory) |
| page-context reads | **2** (`get_page_context` before *and* after the action; text/URL extraction) | 1 (`page.url`) |
| trajectory persistence | `checkpoint()` writes `data_point.json` + `run_state.json` **every step** | none (optional traces only) |
| captcha gate | per-step `wait_for_captcha` (Browserbase) | none |
| load settle | `wait_for_load` | one `domcontentloaded` wait |

So the official harness spends several extra CDP/DOM round-trips + disk writes per step
(~6 s/step of non-model time on t1 vs our ~0.3 s). That overhead **buys** it things we
deliberately don't pay for on the interactive path: an auditable pre/post observation
trajectory, structured page-text observations, captcha handling, and resumability. We
optimize for chat-UI latency; they optimize for reproducible research/production runs.
Both keep only the most-recent-3 screenshots, so image count is *not* the difference.

## Takeaways — what to borrow to improve our harness

Our end goal is **handing tasks over to the user** and **completing complex end-to-end
flows**. The official harness is strong at exactly these, and most of what it does is
adoptable without giving up our latency edge.

### 1. Make `ask_user_question` a *resumable handover*, not a dead-end (highest value) — ✅ DONE (ADR 0018)
Our vendored **system prompt already contains the full "critical points" guidance** — the
model is told to pause and `ask_user_question` when (1) required info is missing, (2) the
task is ambiguous, or (3) an irreversible action isn't authorized. So **our model already
asks.** The gap is purely in the loop: today we treat the question as terminal —

```python
# fara_browse.py (today)
if action == "ask_user_question":
    return True, f"I need input to continue: {args.get('question', '')}"  # run ends, state lost
```

The official harness instead sets `WAITING_FOR_USER`, **persists state** (chat history,
`facts`, `current_step`), returns the question, and on the user's reply **resumes** —
appending the reply as a user message (its text preserved even after screenshots are
trimmed) and continuing from where it stopped. Adopting this turns handover from a
restart into a real collaboration and is the single biggest unlock for complex flows.

### 2. Confirm at critical points instead of hard-refusing
Our purchase guardrail hard-refuses (`_PURCHASE_REFUSAL`) and returns. The critical-points
model is better: **pause and ask for confirmation**, then (with #1) let the user authorize
and the flow *complete* — e.g. fill the cart, pause before "Place order", user says go,
finish. Same safety, but complex flows can actually finish with a human in the loop.

### 3. Persist a resumable checkpoint per turn — ✅ DONE (warm relaunch, ADR 0018)
The official `RunContext.checkpoint()` writes state every step and auto-resumes. Our browse
turn is ephemeral: a server restart or the user stepping away loses it (we hit exactly this
— a session restart killed the official run mid-flight; a checkpointed run would have
resumed). Persisting `{chat_history, facts, step, profile, url}` lets long flows survive
interruption and pairs with the handover in #1.

### 4. Give working memory (`facts`) back to the model
Both harnesses expose `pause_and_memorize_fact`; the official one collects them into
`state.facts`. We should **collect and re-inject** them as a compact "known so far" block
on later steps, so multi-hop tasks (read price on page A, compare on page B) keep
intermediate results after screenshots are trimmed.

### 5. Targeted text observation as a fallback for hard pages
The one class both harnesses failed is dense forms. A targeted text / accessibility-tree
observation (the official `get_page_markdown` / `read_page_answer_question` style) offered
*only* when pixel grounding stalls could unstick forms without slowing the common path.

### 6. Validate actions against the schema; terminate cleanly on garbage
The official loop extracts the allowed-action enum from the prompt and validates every
action, ending the trajectory with the raw text as the answer on a parse or invalid-action
error (`terminate_on_parse_error`). We currently emit "(unsupported action)" and continue.
A small robustness win.

### What NOT to borrow
Keep our low-latency interactive path: **do not** adopt per-step pre+post screenshots, the
double page-context reads, or per-step disk persistence on the live path — that is the
source of the official harness's 2–3× slowdown and our UX advantage. Reserve heavy
trajectory capture for the opt-in eval/record mode (`fara_save_traces`). The goal is to
adopt their *resumability + handover* (state, not I/O), not their *per-step cost*.

## Suggested sequencing
1. Resumable `ask_user_question` handover (#1) + surface the question in the chat/live UI.
2. Critical-point confirmation replacing the hard purchase refusal (#2).
3. Per-turn checkpoint/resume (#3) + `facts` re-injection (#4).
4. Text-observation fallback for forms (#5) and action validation (#6).

Reproduce this comparison any time with the recipe in `README.md`
(`run_fara` + `run_vendored` + `compare`).
