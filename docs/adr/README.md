# Architecture Decision Records

This log captures the significant technical decisions behind Oli — the *why*,
not just the *what*. Each record is short and immutable: if a decision changes,
we add a new ADR that supersedes the old one rather than editing history.

Format follows [Michael Nygard's ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

| # | Title | Status |
|---|-------|--------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-build-agent-from-scratch-then-adopt-framework.md) | Build the agent from scratch, then adopt a framework | Accepted |
| [0003](0003-langgraph-for-agent-orchestration.md) | Use LangGraph for agent orchestration | Accepted |
| [0004](0004-postgres-pgvector-for-data-and-memory.md) | Postgres + pgvector for data and memory | Accepted |
| [0005](0005-multi-user-saas-with-auth.md) | Multi-user SaaS with authentication | Accepted |
| [0006](0006-groq-default-swappable-llm-provider.md) | Groq as the default, swappable LLM provider | Accepted |
| [0007](0007-deploy-free-tier-first.md) | Deploy free-tier first, evolve orchestration later | Accepted |
