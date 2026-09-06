# 2. Build the agent from scratch, then adopt a framework

Date: 2026-09-06
Status: Accepted

## Context

The core of Oli is an agent loop: assemble context → call the LLM → run any
requested tools → repeat until the model answers. This can be hand-written in
~50 lines against an OpenAI-compatible SDK, or delegated to a framework
(LangChain/LangGraph, Pydantic AI, etc.).

## Decision

Build the loop from scratch first, then migrate it to a framework once the
fundamentals are proven. The prototype baseline (commit before Phase A) is the
hand-rolled version; production migrates to LangGraph (see ADR 0003).

## Consequences

- Demonstrates understanding of agent internals, not just framework glue.
- Produces an honest git history: "prototype → productionization."
- The hand-rolled loop is throwaway once the framework lands, but it de-risked
  the design and clarified exactly what the framework must provide.
