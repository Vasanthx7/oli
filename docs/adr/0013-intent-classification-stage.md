# 13. Intent classification stage before the agent

Date: 2026-09-11
Status: Accepted

## Context

The agent was a bare ReAct loop (ADR 0002/0003): the raw user message went straight
into `START -> agent -> tools -> agent`. Nothing read *what* the user wanted before
the model saw it, so there was:

- no signal to route on (every turn hit the same model and the same flat rails), and
- nothing meaningful to trace — observability (ADR: Phase 1) could see tokens and
  latency but not the *kind* of turn driving them.

This is the first of three phases (see `docs/plans/intent-harness-observability.md`);
it deliberately lands after observability so the intent signal is measurable from day
one, and before the harness work (Phase 3) that will consume it for model routing.

## Decision

Add a **classify** node as the graph's new entry point:
`START -> classify -> agent -> tools -> agent -> END`.

- **`intent.py`** classifies the latest user message into a structured `Intent`
  (`category` ∈ chat/tools/browse/memory, `complexity` ∈ simple/hard, `needs_tools`,
  `confidence`, `normalized_query`) using LangChain `with_structured_output` against
  the chat endpoint. Scope is **classification + normalization only** — no
  clarification questions, no planner (both deferred).
- The `Intent` is stored in graph **state** (`State.intent`), so Phase 3's agent node
  can route on it without re-classifying.
- **Best-effort, never fatal**: any failure (bad JSON, endpoint down, no key) falls
  back to a safe `chat`/`simple` intent with confidence 0. The agent keeps every tool
  regardless, so a wrong or missing intent affects *routing*, never *capability*.
- **Observability**: on the classify node completing, `run_turn` increments
  `oli_intents_total{category,complexity}`, logs `intent_classified` (correlated by
  request id), and emits a new `{"type":"intent",...}` SSE event.

Because the classifier makes its own LLM call inside the graph, `run_turn` gates the
user-facing token stream and final-answer detection to the **agent** node (a missing
`langgraph_node` is treated as the agent, preserving the existing fake-graph tests).
The classifier's tokens therefore never leak to the UI, and its structured-output
message is never mistaken for the final answer. Token/latency metrics still count the
classifier's call — it is real cost.

## Consequences

- **Routable + traceable turns**: every turn now carries a typed intent that Phase 3
  can route on and that shows up in metrics, logs, and traces.
- **Extra LLM call per turn**: classification adds one cheap call. Phase 3 moves it to
  the fast tier (`llama-3.1-8b-instant`) so the overhead is small; on the same tier as
  today it is a modest, measurable cost — which is the point of instrumenting first.
- **Graceful degradation**: if classification fails the assistant behaves exactly as
  before (full-capability chat), so the feature can't reduce reliability.
- **UI is forward-compatible**: the `intent` SSE event is additive; the current UI
  ignores unknown event types, and a future panel can surface it.
