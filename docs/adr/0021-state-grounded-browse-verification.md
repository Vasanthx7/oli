# 21. State-grounded verification for browse

Date: 2026-10-04

## Status

Accepted

## Context

`browse` (the local Fara-1.5 computer-use loop, ADR 0016) judges success by the
model's **self-reported terminate text**: the loop returns whatever the model writes
(`fara_browse._drive`), and the eval validators string-match that answer
(`run_vendored`). The code already flagged the hole — `_VERIFY_SUFFIX` and
`scenarios._stops_before_purchase` exist precisely because Fara will claim *"added to
cart"* when the live cart is empty (typically a silently signed-out session). A
hallucinated success therefore passes **both** the agent's own check and the eval.

This surfaced while evaluating rival models (ADR 0020): we're staying on Fara, and the
highest-leverage reliability fix is to judge against **real page state**, not prose.

## Decision

Read the real cart deterministically with Playwright JS — **no model, no cloud**
(preserves ADR 0016's local-only stance) — and feed that truth into two places.

- **New reader `src/oli/tools/page_state.py`.** A single JS scraper gathers candidate
  DOM signals into a plain dict; pure Python (`parse_cart`) decides. Split this way the
  risky parsing is unit-testable offline with canned dicts. Site keying uses an explicit
  host-substring map (`site_for_host`), **not** `profiles._domain_root` (which reduces
  `next.cult.fit` to `"fit"`). Per-site parsers for amazon/zomato + a conservative
  generic fallback that trusts a reading only when it finds **both** a count and a
  currency figure.
- **Advisory contract.** The reader can only ever *catch a false success*: on any doubt
  it returns `ok=False` and callers leave the model's answer untouched. Selector drift or
  an unfamiliar site degrades to a silent no-op — it must never fail a genuinely-correct
  run.
- **Production reconciliation.** Gated by `settings.fara_verify_state` (default on;
  `FARA_VERIFY_STATE=false` is an instant kill-switch). At the terminal "done", when the
  goal is cart/order-related **and** the model claims success **but** the live cart
  contradicts it (empty / "only item" but N>1 / an order got placed on a stop-before-order
  goal), the answer is **amended** (a `[State check]` note appended, original preserved).
  A corroborated claim is never altered.
- **Harness ground-truth grading.** `Scenario.validate_state` grades the cart scenarios
  (`e1/e3/e6/e18`) on the real `count`/`subtotal`, captured via a back-compatible
  `state_out` side channel on `fara_browse._run` (populated before teardown while the page
  is live). Falls back to the string validator when the read fails.

## Consequences

- **Good:** a hallucinated "added to cart" is downgraded in production and FAILS in the
  eval instead of passing — reliability and the harness are fixed by the same code.
  Deterministic, local, cheap (one extra `page.evaluate` on cart-goal terminals; no model,
  no screenshot).
- **Cost/risk:** per-site selectors will drift; the `ok=False`-on-doubt contract makes
  drift a graceful no-op rather than a misfire, and the harness surfaces stale parsers.
  Over-aggressive downgrade is bounded by three gates (flag + cart-goal + claims-success)
  plus `ok` plus amend-only-on-contradiction, with the env kill-switch as backstop.
- **Follow-ups:** (1) real-world validation on the GPU box — run the cart scenarios
  against live Ollama+Fara (`--allow-manual`, watched, authorized account) to confirm a
  signed-out run now scores FAIL, not a false PASS; (2) extend ground-truth grading to the
  Holo eval runner; (3) generic form-field (non-cart) verification is a later extension.
