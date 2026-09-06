# 1. Record architecture decisions

Date: 2026-09-06
Status: Accepted

## Context

Oli is evolving from a working prototype into a portfolio-grade project meant to
demonstrate production-level engineering judgement. The reasoning behind key
decisions is as valuable as the code itself, and it is easy to lose that context
over time.

## Decision

We will keep Architecture Decision Records (ADRs) in `docs/adr/`, one file per
significant decision, using Michael Nygard's lightweight template (Context,
Decision, Consequences). Records are immutable; a superseding ADR replaces an old
one rather than editing it.

## Consequences

- The "why" behind the stack is documented and reviewable.
- Reviewers (and future me) can trace how the architecture evolved.
- A small ongoing cost: each substantial decision gets a short write-up.
