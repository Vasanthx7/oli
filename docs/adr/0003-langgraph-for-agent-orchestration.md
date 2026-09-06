# 3. Use LangGraph for agent orchestration

Date: 2026-09-06
Status: Accepted

## Context

For the production agent (see ADR 0002) we evaluated LangGraph, Pydantic AI, and
keeping the hand-rolled loop. Requirements: streaming to the UI, tool calling,
durable/resumable state (for long-running and scheduled tasks), good tracing, and
a recognizable, transferable skill.

## Decision

Adopt **LangGraph**. Model the agent as a `StateGraph` (recall memory → call model
→ tools → loop) with a checkpointer for durable state, keeping the model layer
provider-agnostic (see ADR 0006) and preserving SSE streaming via
`astream_events`.

## Consequences

- Strong, in-demand skill signal; first-class support for stateful, resumable,
  human-in-the-loop agent workflows we will want later.
- Cost: heavier dependency surface and more abstraction than the hand-rolled loop
  or Pydantic AI. Accepted for the ecosystem, tracing (LangSmith), and resume value.
- Tools and memory are isolated behind interfaces, so the migration touches the
  loop, not the whole codebase.
