# CLAUDE.md

Project guidance for Claude Code. Loaded automatically each session — keep it short and current.

## What this is

**Oli** — a single-user personal assistant: FastAPI + an SSE web UI, a LangGraph agent
with tools (web search/fetch, autonomous `browse` via browser-use, memory), a live
browser view (CDP screencast), and background scheduled tasks. All Python, managed
with `uv`.

## Branching & release flow

**`dev` is the integration branch and the repo default. `main` is the release branch —
merging to `main` triggers the deploy (the `publish` job builds and pushes the image to
GHCR). Keep `main` moving as little as possible.**

- **Start a feature/fix** — branch off `dev`, never off `main`:
  ```
  git checkout dev && git pull
  git checkout -b feature/<name>      # or fix/<name>, chore/<name>
  ```
- **Ship it to `dev`** — open the PR into **`dev`** (the default base, so this is
  automatic). Merge once CI is green.
- **Release to `main`** — periodically open a **`dev` → `main`** PR to batch several
  merged features into one deploy. This is the only thing that should land on `main`.
- **Hotfix** — same shape (`fix/*` off `dev` → PR to `dev` → `dev`→`main` PR). Only
  cut a branch straight off `main` for a true emergency, and back-merge it into `dev`.

```
feature/*  ──PR──▶  dev  ──PR (batched)──▶  main ──▶ deploy (GHCR publish)
```

Rationale: batching feature merges through `dev` means `main` (and the deploy) fires
far less often, and CI runs on the PR rather than on every feature-branch push.

## CI — local-first, release-boundary

Quality is **local-first**: the checks run on your machine via git hooks, and CI only
runs them at the **`main` boundary** (a `dev` → `main` PR, or a push to `main`).
**Feature → `dev` PRs run nothing in CI** — keep `dev` green with the local hooks.

Local gate (`.pre-commit-config.yaml`) — install once per clone:
```
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```
- **on commit:** `ruff` (+ `--fix`), `ruff-format`, whitespace/yaml/toml/merge-conflict.
- **on push:** `mypy src/oli`, `pytest` — the heavier gates, before code leaves your machine.

CI (`.github/workflows/ci.yml`), triggers `push: [main]` + `pull_request`:
- `quality` (`ruff check` / `ruff format --check` / `mypy` / `pytest`) and
  `container` (docker build + Postgres smoke) — guarded to run only when
  `github.event_name == 'push'` **or** `github.base_ref == 'main'` (i.e. main push or a
  PR into `main`).
- `publish` — build & push image to GHCR; **`main` only** (`github.ref == refs/heads/main`).

Net effect: fast local feedback on the way to `dev`; the full suite + image smoke run
once, at the release boundary, right before publish/deploy.

## Local dev commands

```
uv sync --extra dev --frozen        # install (matches CI)
uv run ruff check .                 # lint
uv run ruff format --check .        # format check (drop --check to fix)
uv run mypy src/oli                 # type-check
uv run pytest                       # tests (offline; conftest injects a dummy key + temp DB)
uv run pre-commit run -a            # run the whole local gate by hand
```

Since CI no longer runs on feature → `dev` PRs, **install the hooks** (command above) —
they are what keeps `dev` green.

## Notes

- Architecture and the "why" behind decisions live in `docs/architecture.md` and
  `docs/adr/`. Add an ADR for any notable decision.
- Windows: Playwright/browser-use need the Proactor event loop — already set in
  `main.py`; don't remove it.
