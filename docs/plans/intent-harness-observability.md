# Plan: Intent layer, agent harness & observability

Status: **planned** (2026-09-11). Branch: `feature/intent-harness-observability` off `dev`.

## Motivation

Three layers of the agent are currently thin:

- **Intent** — none. The raw user message goes straight into the LangGraph ReAct
  loop (`agent_graph.py`). Nothing classifies *what* the user wants or normalizes
  the query before the model sees it, so there's no signal to route on and nothing
  meaningful to trace.
- **Harness** — a bare ReAct graph: one system prompt (personality + recalled
  memory), tools bound directly, a flat `RECURSION_LIMIT=16` as the only rail. The
  original plan's cheap/frontier model routing was never built; there are no
  per-turn budgets or per-tool timeouts.
- **Observability** — LangSmith exists but is opt-in/off; Prometheus only counts
  requests, turns, and tool calls. No token usage, no cost, no per-LLM-call latency,
  no error taxonomy, and nothing correlates logs ↔ metrics ↔ traces.

## Guiding decision: instrument before optimize

We build **observability first** so that as the intent layer and routing land we can
measure whether they actually help (latency, token cost, path taken). Building the
features first and instrumenting after would leave us optimizing blind.

Sequence: **Phase 1 observability → Phase 2 intent → Phase 3 harness.** Each phase is
its own commit + tests + (where notable) an ADR, and its own PR into `dev`.

---

## Phase 1 — Observability foundation

**Goal:** one turn is followable end-to-end across logs, metrics, and traces.

- **LangSmith on by default when a key is present.** `langsmith_tracing` becomes
  effectively true whenever `langsmith_api_key` is set; with no key it stays off, so
  CI/tests remain offline. (`config.py`, `tracing.py`.)
- **Correlation.** Thread the existing request-id contextvar (bound by the request-id
  middleware in `main.py`) into structured logs *and* LangSmith run metadata, so a
  single turn links across all three signals.
- **Richer metrics** (`metrics.py`), low-cardinality only:
  - token usage counters (prompt / completion), labeled by model tier;
  - per-LLM-call latency histogram;
  - error counter keyed by exception type.
- **Tests:** metrics render + counters increment offline; tracing stays disabled with
  no key. No new ADR (extends the Phase I observability decision).

## Phase 2 — Intent layer

**Goal:** a structured, traceable intent for every turn.

- **`intent.py`** — a cheap-model **structured classifier** returning an `Intent`:
  `category` (chat / tools / browse / memory), `complexity` (simple / hard),
  `needs_tools` (bool), `confidence`, `normalized_query`. Runs on the fast tier.
- **Graph wiring** — insert a `classify` node: `START → classify → agent`. Intent is
  stored in graph state, emitted as an SSE event, added as a metrics label, and
  attached to the trace (Phase 1's plumbing pays off here).
- **Scope:** classification + query normalization only. Clarification questions and a
  full planner are explicitly deferred.
- **Tests:** classifier returns a valid `Intent` for representative inputs (mocked
  LLM); graph routes through `classify`. **ADR:** "Intent classification stage."

## Phase 3 — Harness: routing + guardrails

**Goal:** right-sized model per turn, with hard safety rails.

- **Model routing** — two tiers in config:
  - fast/default `chat_model` = `llama-3.1-8b-instant` (chat + classifier);
  - `chat_reasoning_model` = `openai/gpt-oss-120b` (current default), used when
    `complexity=hard` or the turn is tool-heavy.
  The `agent` node selects the model by intent. Both env-overridable.
- **Guardrails** (enforced in the graph / `run_turn`, beyond `RECURSION_LIMIT`):
  - `max_tool_calls_per_turn`;
  - a per-turn token budget;
  - a per-tool timeout wrapper.
- **Tests:** routing picks the expected tier per intent; a tool exceeding its timeout
  fails cleanly; budgets abort the turn gracefully with a user-facing message.
  **ADR:** "Cheap/frontier model routing + per-turn guardrails."

## Explicitly out of scope (this pass)

- Tool-call **repair/retry** loop (feed errors back for self-correction) — deferred.
- Intent **clarification** questions and multi-step **planner/decomposer** — deferred.

## Model tiers (defaults)

| Tier      | Setting                 | Default                  | Used for                          |
|-----------|-------------------------|--------------------------|-----------------------------------|
| Fast      | `chat_model`            | `llama-3.1-8b-instant`   | chat, intent classification       |
| Frontier  | `chat_reasoning_model`  | `openai/gpt-oss-120b`    | `complexity=hard` / tool-heavy    |
