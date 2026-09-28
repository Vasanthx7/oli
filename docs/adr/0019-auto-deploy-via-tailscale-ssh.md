# 19. Auto-deploy on merge to main via Tailscale SSH, pinned to the commit SHA

Date: 2026-09-28

## Status

Accepted. Extends ADR 0012 (deploy to AWS) and supersedes its "auto-deploy-on-push
was explicitly deferred / deploying is a manual SSH step" consequence.

## Context

ADR 0012 stood up the AWS box (single EC2, docker-compose, Caddy, home Ollama over
Tailscale) but stopped at image publish: CI pushed `ghcr.io/vasanthx7/oli:latest` and
`:<sha>`, and a human then SSHed in to run `docker compose pull && up -d`. Two gaps:

- **The deploy was manual** — the last mile was undone by hand on every release.
- **Prod tracked `:latest`** — "what is actually running" was ambiguous and there was no
  clean rollback target.

Getting a CI runner onto the box is non-trivial: security-group ingress on 22 is
CIDR-locked to the operator's IP, so a GitHub-hosted runner's IP can't SSH directly.
The box already runs `tailscale up --ssh`, which is the opening.

## Decision

- **Deploy over Tailscale SSH from GitHub Actions.** A `deploy` job joins the tailnet
  ephemerally via a tagged (`tag:ci`) Tailscale OAuth client, then `ssh ubuntu@<box>` —
  authenticated by tailnet identity through an ACL `ssh` rule, so **no SSH key or
  security-group change** is needed. Public SSH stays closed.
- **Deploy a pinned commit SHA, not `:latest`.** `docker-compose.prod.yml` uses
  `image: ghcr.io/vasanthx7/oli:${OLI_IMAGE_TAG:-latest}`; the deploy writes
  `OLI_IMAGE_TAG=<sha>` into `/opt/oli/.env`, so reboots and manual `up -d` keep running
  the exact build. `:latest` remains the graceful default before the first rollout.
- **Gate the release on health.** After `compose pull app && up -d` the job polls the
  public `https://<domain>/health/ready` (auth-exempt), failing the run if the new build
  doesn't become ready — exercising the full DuckDNS → Caddy TLS → app → DB path.
- **Rollback is `workflow_dispatch`.** Running the workflow manually with an `image_tag`
  input redeploys any prior SHA without rebuilding (build/publish are skipped).
- **Migrations stay in the image `CMD`** (`alembic upgrade head` before uvicorn) — no
  separate migration step in the pipeline.

## Consequences

- Merging to `main` now deploys end-to-end with a health gate; the RUNBOOK's manual
  rollout becomes the break-glass path, not the norm.
- Prod is reproducible and rollback is one manual workflow run to a known-good SHA.
- New operational secrets to hold: `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`,
  `DEPLOY_HOST`, `DEPLOY_DOMAIN`, plus a Tailscale ACL `ssh` rule (`tag:ci` → `ubuntu`).
- **Backups remain unaddressed.** Automated deploys now run migrations unattended against
  a single `pgdata` volume with no snapshot — the blast radius of a bad migration grows.
  A `pg_dump`-before-deploy (or scheduled dumps) is the recommended next follow-up, still
  deferred here to keep this change contained.
- The deploy is push-based and stateless on the box (no Watchtower/agent); if a rollout
  is needed while GitHub is unreachable, the manual one-liner in the RUNBOOK is the
  fallback.
