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

## Database migrations

```bash
uv run alembic upgrade head                              # apply
uv run alembic revision --autogenerate -m "describe"     # create after model change
uv run alembic downgrade -1                              # roll back one
```

Dev on SQLite auto-creates tables via `init_models`; production (Postgres) is
migration-managed only.

## Quality gate (what CI runs)

```bash
uv run ruff check .          # lint
uv run ruff format --check . # format
uv run mypy src/oli          # types
uv run pytest                # tests (offline; dummy key + temp DB)
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
