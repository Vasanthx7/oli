# 8. Defer multi-user; stay single-user for now

Date: 2026-09-06
Status: Accepted (supersedes ADR 0005)

## Context

ADR 0005 proposed building Oli as a multi-user SaaS with authentication and
per-user data isolation. In practice Oli is a *personal* assistant with a single
operator (the owner of the server it runs on). Multi-tenancy adds a substantial
surface — accounts, sessions, password/OAuth flows, `user_id` scoping on every
table and query, and the tests to prove isolation — none of which serves the
current single-user goal.

## Decision

Stay **single-user** for now and **defer** authentication and multi-tenancy. The
app remains private by deployment (reached only over the owner's network / behind
the reverse proxy), rather than by application-level auth. The data model keeps no
`user_id` columns until multi-user is actually needed.

This supersedes ADR 0005. If Oli is ever opened to multiple users, revisit with a
new ADR: add a `users` table, `user_id` foreign keys, a `current_user` dependency
that scopes all access, and auth (argon2 + sessions/JWT).

## Consequences

- Less code and fewer moving parts; effort goes to the DevOps/deployment goals.
- The app must be kept private at the network/deployment layer, since there is no
  application-level access control. This is a deliberate constraint, not an oversight.
- Adding multi-user later is a clean, additive migration (new columns + auth layer),
  not a rewrite — the async data layer from ADR 0004 already isolates data access.
