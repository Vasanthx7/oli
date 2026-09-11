# 14. Cheap/strong model routing + per-turn harness guardrails

Date: 2026-09-11
Status: Accepted

## Context

Phase 3 of the intent/harness/observability initiative
(`docs/plans/intent-harness-observability.md`). Two harness weaknesses remained
after the intent stage (ADR 0013) gave every turn a typed intent:

- **One model for everything.** Simple chit-chat and hard multi-tool turns both hit
  the same `chat_model` (`gpt-oss-120b` by default). The original plan always
  intended a cheap/frontier split; nothing consumed the intent signal yet.
- **One flat rail.** `RECURSION_LIMIT=16` was the only safety bound. A tool could
  hang indefinitely, and a pathological loop could burn unbounded tokens, with no
  per-turn ceiling.

## Decision

**Model routing (two tiers on the chat endpoint).** Keep `chat_model` as the strong
tier and add `chat_fast_model` (default `llama-3.1-8b-instant`). The agent node routes
per turn from the classified intent:

- `_use_reasoning_tier(intent)` → strong tier when `complexity == "hard"`, when
  `needs_tools` is set, or when intent is missing/failed (safe default: don't
  under-power an unclassified turn); fast tier otherwise.
- The **intent classifier itself runs on the fast tier** — it is a cheap, high-volume
  pre-pass and must never burn the strong model.

Both tiers are bound once at graph build and share the chat endpoint/key. On a
hybrid-local setup (ADR 0010, `chat_*` → Ollama) `chat_fast_model` must name a model
that exists locally; the Groq default only exists on Groq. Setting it equal to
`chat_model` disables tiering.

**Per-turn guardrails** (config, enforced in `run_turn` / the tool adapter):

- `max_tool_calls_per_turn` (default 8) — the turn ends gracefully after N tool calls.
- `per_turn_token_budget` (default 0 = off) — once total prompt+completion tokens for
  the turn cross the budget, it ends gracefully. Off by default so a legitimately long
  turn isn't truncated; opt in where cost control matters.
- `tool_timeout_seconds` (default 180, 0 = off) — a hard `asyncio.wait_for` ceiling per
  tool call, returned to the model as a string. Generous so `browse` (already bounded
  by its own `MAX_STEPS`) isn't cut short, while still capping a truly hung tool.

A guardrail stop increments `oli_guardrail_stops_total{kind}`, logs `guardrail_stop`
(request-id correlated), and yields the normal `done` event with a short note, so the
UI path is unchanged. When `run_turn` breaks mid-stream it calls `aclose()` on the
event stream so the suspended graph is torn down cleanly.

## Consequences

- **Cost/latency win on the common path.** Simple turns and every classification run on
  the small model; the strong model is reserved for hard/tool turns. Phase 1's token
  metrics (`oli_llm_tokens_total{model}`) make the shift measurable per tier.
- **Bounded turns.** A hung tool, a runaway tool loop, or a token blow-up now each have
  an explicit ceiling instead of relying on the recursion limit alone.
- **Extra config surface**, and a hybrid-local caveat: `chat_fast_model` must exist on
  whatever endpoint `chat_*` points at. Documented in `.env.example`.
- **Routing is only as good as the intent.** A misclassification can under- or
  over-power a turn; the safe default (unclassified → strong) errs toward capability.
  Tool-call *repair/retry* remains deliberately out of scope (deferred).
