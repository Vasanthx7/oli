# 18. Resumable browse handover (ask the user mid-task, then continue)

Date: 2026-09-13

## Status

Accepted. Builds on ADR 0016 (native Fara browse engine) and the harness review in
ADR 0017 / `evals/browse/E2E_CASES.md`.

## Context

Fara's vendored system prompt already tells the model to **pause and `ask_user_question`**
at "critical points": missing information, an ambiguous task, or an irreversible action
that isn't authorized. So the model *does* ask. But our loop treated the question as a
**terminal** action — it returned `"I need input to continue: …"`, the turn ended, and the
browser (with all its state) was torn down. The user's answer arrived in the next turn as
an ordinary chat message with no memory of the browse, so the task effectively restarted
from zero. This blocked exactly the flows we care about — booking with confirmation,
supplying a missing email, disambiguating "near me", confirming before checkout — a third
of the E2E cases (e5, e9, e10, e11, e12).

## Decision

Make a browse **pause and resume** instead of ending on `ask_user_question`.

- **Run state is a resumable object.** The observe-think-act loop's state (chat history,
  step counter, stall trackers, model tier, and the live browser/page handles) lives in a
  `_RunState`. On `ask_user_question` the loop returns `("paused", question)` **without
  closing the browser**; the state is stashed in a module-level `_paused` (single-user →
  at most one). `has_paused_browse()` is the signal.
- **The next user message resumes it.** `run_turn` checks `has_paused_browse()` first; if
  set, it routes that message into `resume_paused_browse(reply)` — which appends the reply
  plus a fresh screenshot of the still-open page and continues the loop from where it
  stopped — instead of starting a fresh agent turn. A resume can pause again (another
  question) or finish; either way the result is natural language and is surfaced directly
  (no extra LLM round-trip).
- **The browser stays live across the pause**, still attached to the live view, so the
  user can *see* where the agent is stuck while answering — and take-control still works.
- **Bounded + safe.** The step budget is shared across the whole (possibly multi-pause)
  session, so back-and-forth can't run forever. A brand-new browse discards any stale
  paused run (`discard_paused_browse`), and a page that died while paused fails cleanly.
- **Evals unchanged.** The single-shot `_run` (used by `run_vendored.py`) still treats an
  ask as terminal — there is no interactive user in a benchmark — so results stay
  comparable. Only the production `browse_fara`/`resume_paused_browse` path is resumable.

## Consequences

- **Handover works end to end:** the agent asks, the user answers, the *same* browse
  continues — enabling confirm-before-irreversible and clarify-then-proceed flows. This is
  the top lever identified in the harness review for completing complex flows.
- **This also delivers the critical-point confirmation UX** (ADR 0017 takeaway #2) for
  free: the model already asks before unauthorized irreversible actions; now that ask is
  actionable rather than a dead end. The hard purchase guardrail remains as a backstop.
- **A browser can be held open between turns** while awaiting a reply — acceptable for a
  single-user app (one browse at a time); a new/abandoned task reclaims it.
- **Durable across a restart via *warm relaunch*.** The live browser can't survive a
  process restart, so we don't try to resurrect the exact page. Instead, on pause a
  lightweight record — goal, question, profile, last URL — is persisted to a JSON file
  (`data/pending_handover.json`; no schema/migration, matches the one-browse model). After
  a restart the user's reply triggers a *warm relaunch*: a fresh browse seeded with the
  original goal + the question + the reply + last URL (`resume_after_restart`), routed the
  same way (`has_pending_browse()` / `resume_pending_browse`). Not pixel-exact resume, but
  the task continues instead of being silently dropped. (ADR 0017 takeaway #3.)
- **Ambiguous replies:** a paused browse treats the next message as the answer; if the
  user pivots to something unrelated, the model will usually terminate or re-ask. A
  reply-vs-new-request classifier is a possible refinement.
