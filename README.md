# Oli

**Oli** (Tamil: ஒளி, "light / radiance") is a personal AI assistant that runs as a web
app: chat with streaming responses, a configurable personality, persistent conversation
history, long-term memory, voice, proactive scheduled tasks, and live-internet tools —
web search, page fetch, and autonomous browsing via
[browser-use](https://github.com/browser-use/browser-use).

Powered by [Groq](https://groq.com) (OpenAI-compatible API).

## Stack

- **Backend:** FastAPI + Uvicorn (Python)
- **LLM:** Groq (`gpt-oss-120b` by default), swappable via env
- **Browsing:** browser-use (headless Chromium)
- **Search / fetch:** ddgs + httpx + trafilatura
- **Storage:** SQLite (conversations + messages)
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

## Development

```bash
uv sync --extra dev          # install app + dev tools
uv run ruff check .          # lint
uv run ruff format .         # format
uv run mypy src/oli          # type-check
uv run pytest                # tests (fully offline — no API key needed)
uv run pre-commit install    # enable the pre-commit hooks
```

CI (GitHub Actions, `.github/workflows/ci.yml`) runs lint, format, type-check,
and tests on every push and PR. Tests inject a dummy key and a throwaway database,
so they never make network calls or touch real data.

Architecture decisions are recorded in [`docs/adr/`](docs/adr/).

## Roadmap

**Feature-complete (prototype):**

- [x] Chat brain: streaming, personality, history
- [x] Tools: search, fetch, browse
- [x] Long-term memory (local embeddings, auto-recall + remember/recall tools)
- [x] Voice (Groq Whisper STT + browser speech synthesis, hands-free loop)
- [x] Proactive/ambient (scheduler: daily/interval tasks → notifications)

**Productionization (in progress):**

- [x] Phase A — Foundation: typed settings, structured logging, ruff/mypy, tests, CI, ADRs
- [ ] Phase B — Migrate agent to LangGraph
- [ ] Phase C — Postgres + pgvector + multi-tenant data model
- [ ] Phase D — Auth & multi-user SaaS surface
- [ ] Phase E — Background jobs (Arq + Redis)
- [ ] Phase F — Docker + docker-compose
- [ ] Phase G — CI/CD delivery (build → registry → deploy)
- [ ] Phase H — Deploy (free-tier first) + Terraform IaC
- [ ] Phase I — Observability (metrics, logs, tracing, alerts, backups)
- [ ] Phase J — Portfolio polish
