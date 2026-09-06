# 5. Multi-user SaaS with authentication

Date: 2026-09-06
Status: Superseded by [ADR 0008](0008-defer-multi-user-stay-single-user.md)

## Context

The prototype is single-user with no authentication — unsafe to expose publicly
and a smaller production surface. As a showcase, demonstrating auth, tenancy, and
per-user data isolation is valuable.

## Decision

Build Oli as a **multi-user application**: accounts with hashed passwords
(argon2), session/JWT auth, and every data access (conversations, messages,
memories, scheduled tasks) scoped by `user_id`. A `current_user` dependency
enforces isolation at the API boundary.

## Consequences

- Exercises real production concerns: authentication, authorization, tenant
  isolation, sessions, rate limiting.
- Larger data model and test surface than single-user.
- Requires care that no query crosses tenant boundaries; enforced centrally via
  the auth dependency and covered by tests.
