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

## CI

`.github/workflows/ci.yml` runs on **every pull request** and on **pushes to `main`**
(not on feature-branch or `dev` pushes):

- A feature → `dev` PR runs CI on the feature; the `dev` → `main` PR re-runs CI on the
  integrated `dev` state before release — so every merge is gated by a PR run without
  paying an extra run on each `dev` push. `main` stays in `push` because the deploy
  (`publish`) job triggers there.


- `quality` — `ruff check`, `ruff format --check`, `mypy src/oli`, `pytest`.
- `container` — docker build + Postgres smoke test.
- `publish` — build & push image to GHCR; **`main` only** (`github.ref == refs/heads/main`).

## Local dev commands

```
uv sync --extra dev --frozen        # install (matches CI)
uv run ruff check .                 # lint
uv run ruff format --check .        # format check (drop --check to fix)
uv run mypy src/oli                 # type-check
uv run pytest                       # tests (offline; conftest injects a dummy key + temp DB)
```

Pre-commit hooks are configured (`.pre-commit-config.yaml`) — run `uv run pre-commit run -a`
before pushing to catch what CI checks.

## Notes

- Architecture and the "why" behind decisions live in `docs/architecture.md` and
  `docs/adr/`. Add an ADR for any notable decision.
- Windows: Playwright/browser-use need the Proactor event loop — already set in
  `main.py`; don't remove it.
