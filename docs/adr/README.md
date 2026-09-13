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
| [0005](0005-multi-user-saas-with-auth.md) | Multi-user SaaS with authentication | Superseded by 0008 |
| [0006](0006-groq-default-swappable-llm-provider.md) | Groq as the default, swappable LLM provider | Accepted |
| [0007](0007-deploy-free-tier-first.md) | Deploy free-tier first, evolve orchestration later | Accepted |
| [0008](0008-defer-multi-user-stay-single-user.md) | Defer multi-user; stay single-user for now | Accepted |
| [0009](0009-live-browser-via-cdp-screencast.md) | Live browser (human-in-the-loop) via CDP screencast | Accepted |
| [0010](0010-hybrid-local-chat-groq-browse.md) | Hybrid inference: local model for chat, Groq for browsing | Superseded by 0017 |
| [0011](0011-watch-and-take-control-of-agent-browse.md) | Watch (and take control of) an autonomous browse | Accepted |
| [0012](0012-deploy-to-aws.md) | Deploy to AWS: single EC2 instance, home Ollama over Tailscale | Accepted |
| [0013](0013-intent-classification-stage.md) | Intent classification stage before the agent | Accepted |
| [0014](0014-model-routing-and-harness-guardrails.md) | Cheap/strong model routing + per-turn harness guardrails | Accepted |
| [0016](0016-native-fara-browse-engine.md) | Native Fara-1.5 browse engine (local, no cloud fallback) | Accepted |
| [0017](0017-cloud-provider-failover-local-vision-only.md) | Cloud provider failover for chat; local model for computer-use only | Accepted |
| [0018](0018-resumable-browse-handover.md) | Resumable browse handover (ask the user mid-task, then continue) | Accepted |
