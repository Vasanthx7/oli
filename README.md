# Oli

[![CI](https://github.com/Vasanthx7/oli/actions/workflows/ci.yml/badge.svg)](https://github.com/Vasanthx7/oli/actions/workflows/ci.yml)

**Oli** (Tamil: ஒளி, "light / radiance") is a personal AI assistant that runs as a web
app: chat with streaming responses, a configurable personality, persistent conversation
history, long-term memory, voice, proactive scheduled tasks, and live-internet tools —
web search, page fetch, and autonomous browsing via
[browser-use](https://github.com/browser-use/browser-use).

Powered by [Groq](https://groq.com) (OpenAI-compatible API).

> **Docs:** [Architecture](docs/architecture.md) · [Runbook](docs/RUNBOOK.md) · [Decision records](docs/adr/)

## Stack

- **Backend:** FastAPI + Uvicorn (Python)
- **Agent:** LangGraph (`StateGraph`: agent ⇄ tools, ReAct-style), streaming via `astream_events`
- **LLM:** Groq (`gpt-oss-120b` by default) via a provider-agnostic OpenAI-compatible client, swappable via env — supports a **hybrid** split (local model for chat, Groq for browsing; see below)
- **Browsing:** browser-use (headless Chromium)
- **Search / fetch:** ddgs + httpx + trafilatura
- **Storage:** async SQLAlchemy — SQLite for dev/test, PostgreSQL + pgvector for production (Alembic migrations)
- **Frontend:** static HTML/JS chat UI with SSE streaming

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
# 1. Install dependencies
uv sync

# 2. Install the browser used by browser-use
uv run playwright install chromium

# 3. Configure your API key
cp .env.example .env
#   then edit .env and set GROQ_API_KEY

# 4. Run
uv run uvicorn oli.main:app --reload
```

Open http://127.0.0.1:8000.

## Project layout

```
src/oli/
  main.py          FastAPI app: static UI + /api/chat (SSE) + conversation endpoints
  agent.py         the core loop: context -> LLM -> tool calls -> loop -> stream
  llm.py           Groq streaming client (OpenAI-compatible)
  personality.py   loads the system prompt from personality.md
  storage.py       SQLite: conversations + messages
  embeddings.py    local text embeddings (fastembed / bge-small)
  memory.py        long-term memory: remember, recall, auto-extract facts
  stt.py           speech-to-text via Groq Whisper
  scheduler.py     proactive scheduler: runs tasks on a schedule -> notifications
  profiles.py      persistent browser profiles: one-time human login -> reusable cookies
  live_browser.py  live browser view: CDP screencast + input over a WebSocket
  tools/
    web_search.py  DuckDuckGo search
    web_fetch.py   fetch + extract clean article text
    browse.py      browser-use autonomous browsing
    memory_tools.py  remember + recall_memory tools
web/               chat UI (index.html, app.js, style.css)
personality.md     the assistant's persona (edit to taste)
```

## Voice

Click the 🎙 mic to start a hands-free conversation. Your speech is recorded in
the browser (with automatic silence detection), transcribed by **Groq Whisper**
(`whisper-large-v3-turbo`) on the server, run through the normal agent loop — so
**tools and memory work in voice too** — and the reply is spoken back using the
browser's built-in speech synthesis (local, no extra cost). After Oli finishes,
it listens again automatically.

This is turn-based voice. True duplex realtime with barge-in (interrupt mid-reply)
would need a realtime speech provider (OpenAI Realtime / Gemini Live) — noted on
the roadmap.

## Proactive tasks

Oli can act on a schedule without being asked. From the **⏰ Scheduled tasks**
panel, create a task with a prompt and a schedule:

- **Daily** at a chosen time (e.g. a 7:00 AM news briefing)
- **Every** N minutes/hours (e.g. watch a page and report changes)

A background scheduler runs due tasks through the full agent loop and files each
result as a **notification** (the 🔔 bell, with an unread badge). Each task keeps
its own conversation thread so it has continuity across runs. Run any task
immediately with **Run now**. Manage via `GET/POST/DELETE /api/tasks` and
`/api/notifications`.

Because the scheduler lives inside the server process, proactive tasks run
whenever the server is up — which, on the always-on VM, is all the time.

## Logged-in browsing (browser profiles)

By default `browse` uses a fresh, anonymous browser, so it can only reach public
pages. To let Oli act on sites **you're logged into** (your timeline, a dashboard
behind a login), give it a **browser profile** from the **🔐 Browser profiles**
panel:

1. **Add** a profile — a name and the site's login URL (e.g. `https://x.com/login`).
2. **Log in** — Oli opens a real browser window; you sign in **yourself**, once.
3. **Done** — Chromium persists the session's cookies to `data/profiles/<name>/`.

From then on, `browse` can reuse that authenticated session: the model just names
the profile (`browse(goal, profile="twitter")`) — it can also pick one on its own
when a task clearly needs your account.

**Your password is never sent to the AI.** The human performs the login in a real
browser; only the resulting *cookies* are stored, and the model only ever refers
to a profile *by name* — there is no field anywhere a credential passes through.
(Cookies are bearer tokens, so `data/profiles/` is gitignored and should be
treated as a secret; on a shared host, restrict its permissions.)

> **Headless note:** the one-time login needs a machine **with a display** (your
> PC). On a headless VM, either create the profile locally and copy the folder up,
> or use the planned in-browser (noVNC) login flow. CAPTCHA and 2FA remain hard
> stops for any automated browser — that's expected, not a bug.

## Live browser (watch & drive)

The **🖥 Live browser** panel streams a real browser running on the server into
the UI — you see it live on a `<canvas>` and can click, scroll, and type into it.
It uses the Chrome DevTools Protocol's **screencast** (JPEG frames over a
WebSocket) with input sent back via CDP, so it runs **fully headless** — the same
on your laptop and on a headless VM, no VNC/virtual-display stack.

Its first job is **in-UI login for profiles**: pick a profile, press **Start**,
log into the site yourself in the live view, then **Stop & save** — the
authenticated session persists to the profile for the agent to reuse. This solves
the "login needs a display" limitation of browser profiles on a headless VM, and
still never exposes your password to the AI.

This is **Level A** of a longer arc toward an Operator-style agent: next, agent
`browse` runs become watchable with a *take-control* pause (A2); full desktop
"computer use" (Level B) is future scope and would need a vision/GUI model beyond
the text-only default (see [ADR 0009](docs/adr/0009-live-browser-via-cdp-screencast.md)).

## Long-term memory

Oli remembers durable facts about you across conversations. Memories are stored
in the same SQLite DB and embedded locally with
[fastembed](https://github.com/qdrant/fastembed) (`bge-small-en-v1.5`, 384-dim) —
no extra API key, no GPU. The model (~90MB) downloads once into `data/models/`.

Three paths keep memory fresh:
- **Automatic recall** — each turn, memories relevant to your message are injected
  into context so Oli "just knows."
- **`remember` / `recall_memory` tools** — the model saves or looks up facts on its own.
- **Automatic extraction** — after each exchange, a background LLM call pulls out any
  durable facts and stores them (deduplicated).

Manage what Oli knows from the **🧠 Memory** panel in the sidebar, or via
`GET/POST/DELETE /api/memories`.

## Local & hybrid models (avoid rate limits)

`browse` (browser-use) makes one LLM call per step, so on Groq's free tier
(~30 req/min) a browse turn can hit `429`s. Oli supports a **hybrid** split
([ADR 0010](docs/adr/0010-hybrid-local-chat-groq-browse.md)): run **chat + memory
on a local model** (no rate limits, no cost) and keep **Groq's strong model for
browsing**, where capability matters most.

The chat and browser models have independent endpoints, each defaulting to
`GROQ_*`. To go hybrid with a local [Ollama](https://ollama.com) model:

```bash
ollama pull qwen2.5:7b-instruct        # tool-calling capable, fits ~6GB VRAM
```
```ini
# .env — chat goes local; browser stays on Groq (inherits GROQ_*)
CHAT_BASE_URL=http://localhost:11434/v1
CHAT_MODEL=qwen2.5:7b-instruct
CHAT_API_KEY=ollama
BROWSER_MAX_RPM=27          # pace browse's Groq calls under the free-tier limit
```

Leave the `CHAT_*` lines out to stay fully on Groq. Any OpenAI-compatible endpoint
works (vLLM, LM Studio, llama.cpp) — it's just a `base_url`.

## Development

```bash
uv sync --extra dev          # install app + dev tools
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy src/oli          # type-check
uv run pytest                # tests (fully offline — no API key needed)
uv run pre-commit install    # enable the pre-commit hooks
```

CI (GitHub Actions, `.github/workflows/ci.yml`) runs two jobs on every push/PR:
lint + format + type-check + tests (fully offline — dummy key, throwaway DB), and
a **container job** that builds the Docker image and smoke-tests it against a real
Postgres service (applies migrations, checks `/health` and `/health/ready`).

### Database

By default the app uses a local SQLite file — no setup needed. For a
production-like Postgres (with pgvector):

```bash
docker compose up -d db                                        # start Postgres
export DATABASE_URL=postgresql+asyncpg://oli:oli@localhost:5432/oli
uv run alembic upgrade head                                    # apply migrations
uv run uvicorn oli.main:app --reload
```

Migrations live in `alembic/`. Create one after changing `models.py` with
`uv run alembic revision --autogenerate -m "describe change"`.

Architecture decisions are recorded in [`docs/adr/`](docs/adr/).

### Observability

- **Metrics:** Prometheus metrics at `/metrics` (HTTP request count/latency, chat
  turns, tool calls). A Prometheus + Grafana stack is available as an optional
  compose profile:

  ```bash
  docker compose --profile monitoring up   # Grafana on :3000, Prometheus on :9090
  ```

- **Tracing:** the LangGraph agent can trace to [LangSmith](https://smith.langchain.com)
  — set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`.
- **Logs:** structured (structlog), JSON in production (`LOG_JSON=true`), each line
  carrying a request id.

## Roadmap

**Feature-complete (prototype):**

- [x] Chat brain: streaming, personality, history
- [x] Tools: search, fetch, browse
- [x] Long-term memory (local embeddings, auto-recall + remember/recall tools)
- [x] Voice (Groq Whisper STT + browser speech synthesis, hands-free loop)
- [x] Proactive/ambient (scheduler: daily/interval tasks → notifications)

**Productionization (in progress):**

- [x] Phase A — Foundation: typed settings, structured logging, ruff/mypy, tests, CI, ADRs
- [x] Phase B — Agent migrated to LangGraph (StateGraph + ToolNode, streaming via astream_events)
- [x] Phase C — Async SQLAlchemy + Alembic migrations, Postgres-ready (SQLite dev / Postgres+pgvector prod), docker-compose
- [~] Phase D — Auth & multi-user SaaS — **deferred; single-user for now** (ADR 0008)
- [x] Phase F — Docker + docker-compose (multi-stage image, verified in CI against Postgres)
- [ ] Phase H — Deploy (free-tier first) + Terraform IaC + HTTPS
- [x] Phase I — Observability: Prometheus metrics + /metrics, LangSmith tracing, JSON logs, Grafana stack (compose profile)
- [x] Phase J — Portfolio polish: architecture diagram, runbook, CI badge, docs
- [ ] Phase E — Background jobs (Arq + Redis) — optional for single-user; bundled with the post-restart Docker work
- [x] Phase K (Level A1) — Live browser: watch & drive a headless browser via CDP screencast; in-UI login for profiles (ADR 0009)
- [ ] Phase K (Level A2) — Watch + take-control of agent `browse` runs
- [ ] Level B (future) — Full computer use: virtual desktop + vision/GUI-grounding model
