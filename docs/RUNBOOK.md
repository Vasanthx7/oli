# Runbook

Operational guide for running, testing, and operating Oli.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A `GROQ_API_KEY` (free at https://console.groq.com/keys) in `.env`
- Docker (only for the Postgres / full-stack / monitoring options)

## Run modes

### 1. Local, SQLite (simplest)

```bash
uv sync
uv run playwright install chromium   # only if you want the browse tool
cp .env.example .env                  # add your GROQ_API_KEY
uv run uvicorn oli.main:app --reload
```

Open http://127.0.0.1:8000. Uses a local SQLite file; tables are auto-created.

### 2. Local app + Postgres in Docker

```bash
docker compose up -d db
export DATABASE_URL=postgresql+asyncpg://oli:oli@localhost:5432/oli
uv run alembic upgrade head
uv run uvicorn oli.main:app --reload
```

### 3. Full stack in Docker

```bash
docker compose up --build            # app + Postgres
# add --profile monitoring for Prometheus (:9090) + Grafana (:3000)
```

The app container runs `alembic upgrade head` on start, then serves on `:8000`.
Build with `--build-arg INSTALL_CHROMIUM=true` if you need the browse tool in the
image.

## Production deploy (AWS)

See [ADR 0012](adr/0012-deploy-to-aws.md) for the reasoning. Single EC2
instance, docker-compose, Caddy auto-HTTPS via DuckDNS, chat routed to the
operator's home-PC Ollama over Tailscale, browse on Groq.

### One-time setup

1. **Home PC**: install [Tailscale](https://tailscale.com/download), run
   `tailscale ip -4` to get its tailnet IP. Run Ollama with `OLLAMA_HOST=0.0.0.0`
   (it binds to localhost only by default) and allow the port through the
   firewall for the Tailscale adapter.
2. **DuckDNS**: sign up, create a subdomain, note the token.
3. **GitHub**: create a classic PAT with only the `read:packages` scope (used
   by the instance to pull the private image from GHCR).
4. **Tailscale admin console**: generate a reusable auth key for the EC2 box.
5. `cd terraform && cp terraform.tfvars.example terraform.tfvars`, fill in real
   values (never commit this file — it's gitignored).
6. `terraform init && terraform apply`. Note the `url` and `elastic_ip` outputs.
7. Wait a minute or two for cloud-init to finish (Docker/Tailscale install +
   first `docker compose up`), then open the `url` output.

### Rolling out a new release (automatic)

A push to `main` runs `.github/workflows/ci.yml`: `quality` → `container` →
`publish` (pushes `ghcr.io/vasanthx7/oli:latest` **and** `:<commit-sha>`) →
**`deploy`**. The `deploy` job joins the tailnet, SSHes to the box over Tailscale,
pins that commit's SHA (`OLI_IMAGE_TAG` in `/opt/oli/.env`), and runs
`compose pull app && up -d`, then gates on `https://<domain>/health/ready`.
Migrations run automatically — the image's `CMD` runs `alembic upgrade head`
before uvicorn. No manual step is needed for a normal release.

Prod runs an **exact pinned SHA**, not `:latest` — `docker-compose.prod.yml` uses
`image: ghcr.io/vasanthx7/oli:${OLI_IMAGE_TAG:-latest}` and the deploy writes the
SHA to `.env`, so reboots and manual `up -d` keep running the same build.

**One-time deploy prerequisites** (in addition to the setup above):

- **Tailscale OAuth client** (admin console → Settings → OAuth clients) with the
  `auth_keys` write scope, tagged `tag:ci`.
- **Tailscale ACL `ssh` rule** so the CI node may open a session as `ubuntu`
  non-interactively (action must be `accept`, not `check`):
  ```jsonc
  "ssh": [{ "action": "accept", "src": ["tag:ci"], "dst": ["tag:server"], "users": ["ubuntu"] }]
  ```
  (Ensure the EC2 box carries `tag:server`, or adjust `dst` to its tag.)
- **GitHub repo secrets:** `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`,
  `DEPLOY_HOST` (the box's MagicDNS name or tailnet IP), `DEPLOY_DOMAIN`
  (`<subdomain>.duckdns.org`).

### Rolling back / redeploying a specific build

Run the **CI workflow manually** (Actions → CI → *Run workflow*) with
`image_tag` set to a known-good commit SHA (or `latest`). This skips build/publish
and just redeploys that tag — same pull/up/health-gate path.

Equivalent manual one-liner on the box if you can't use Actions (mirror the
CI backup step first):

```bash
ssh ubuntu@<box>          # over Tailscale
sudo -i                   # /opt/oli/.env is root-owned 0600; GHCR login + docker are root's
cd /opt/oli
sed -i "s|^OLI_IMAGE_TAG=.*|OLI_IMAGE_TAG=<sha>|" .env
docker compose -f docker-compose.prod.yml pull app
mkdir -p backups && docker compose -f docker-compose.prod.yml exec -T db \
  pg_dump -U oli -d oli | gzip > "backups/oli-$(date +%Y%m%d-%H%M%S).sql.gz"
docker compose -f docker-compose.prod.yml up -d
```

### Database backups & restore

Every deploy takes a `pg_dump` **before** the new image runs its migrations
(`alembic upgrade head` on boot), so a bad migration is recoverable. Dumps are
gzipped to `/opt/oli/backups/oli-<timestamp>.sql.gz`; the **7 most recent** are
kept. If the dump fails the deploy aborts (`set -o pipefail`) — migrations never
run without a fresh snapshot.

Restore the latest backup (destructive — replaces current DB contents):

```bash
ssh ubuntu@<box>
sudo -i
cd /opt/oli
LATEST=$(ls -1t backups/oli-*.sql.gz | head -1)
docker compose -f docker-compose.prod.yml stop app        # stop writers first
gunzip -c "$LATEST" | docker compose -f docker-compose.prod.yml exec -T db \
  psql -U oli -d oli
docker compose -f docker-compose.prod.yml up -d app
```

> **Caveat: backups are on the instance's root volume, not offsite.** They protect
> against a bad migration, **not** loss of the instance/volume itself
> (`terraform destroy`, a disk failure). Copying dumps to S3 (or another host) is
> the recommended next follow-up.

### Operating

Run these as root (`sudo -i` after `ssh ubuntu@<box>`) — `/opt/oli/.env` and the
docker socket / GHCR login belong to root:

```bash
docker compose -f docker-compose.prod.yml logs -f app     # app logs
docker compose -f docker-compose.prod.yml logs -f caddy    # cert/TLS issues
docker compose -f docker-compose.prod.yml exec db psql -U oli -d oli
```

- `terraform destroy` tears everything down. Per-deploy dumps live on the
  instance's root volume, so `destroy` (or losing the volume) still **deletes all
  conversation/memory data** — copy a dump off-box first if you want to keep it.
- If chat errors out, check Tailscale first: `tailscale status` on both the
  instance and the home PC, and confirm Ollama is actually listening on
  `0.0.0.0:11434` on the home PC.

## Database migrations

```bash
uv run alembic upgrade head                              # apply
uv run alembic revision --autogenerate -m "describe"     # create after model change
uv run alembic downgrade -1                              # roll back one
```

Dev on SQLite auto-creates tables via `init_models`; production (Postgres) is
migration-managed only.

## Quality gate

Quality is **local-first**. Install the git hooks once per clone:

```bash
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

- **on commit:** ruff (+ `--fix`), ruff-format, whitespace/yaml/toml checks.
- **on push:** mypy + pytest (the heavier gates, before code leaves your machine).

CI runs the same checks (`quality` + `container`) **only at the `main` boundary** —
a `dev` → `main` PR or a push to `main` — not on feature → `dev` PRs. Run the full gate
by hand any time with:

```bash
uv run ruff check .          # lint
uv run ruff format --check . # format
uv run mypy src/oli          # types
uv run pytest                # tests (offline; dummy key + temp DB)
uv run pre-commit run -a     # everything the local gate checks
```

## Observability

- Metrics: `GET /metrics` (Prometheus format).
- Dashboards: `docker compose --profile monitoring up` → Grafana at
  http://localhost:3000 (anonymous access enabled; admin password `admin`).
- Agent tracing: set `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` in `.env`.
- Logs: structured; set `LOG_JSON=true` for JSON.

## Configuration reference

All via environment / `.env` (see `.env.example`): `GROQ_API_KEY`, `GROQ_MODEL`,
`BROWSER_MODEL`, `STT_MODEL`, `DATABASE_URL`, `HOST`, `PORT`, `ENVIRONMENT`,
`LOG_LEVEL`, `LOG_JSON`, `LANGSMITH_*`.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `GROQ_API_KEY is not set` | Add it to `.env`. |
| Chat returns an error event immediately | Bad/empty API key, or model name not available on Groq. |
| `browse` tool says "unavailable" | Chromium not installed — `uv run playwright install chromium` (or build the image with `INSTALL_CHROMIUM=true`). |
| Docker commands hang / "cannot connect" | Docker engine not running; on Windows ensure virtualization is enabled and Docker Desktop shows "Engine running". |
| Migrations fail on fresh Postgres | Ensure the DB is reachable and `DATABASE_URL` uses the `postgresql+asyncpg://` scheme. |
| First chat is slow | The embedding model (~90MB) downloads on first use; it is baked into the Docker image to avoid this in containers. |
