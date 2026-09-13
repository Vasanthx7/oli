# 17. Cloud provider failover for chat; local model for computer-use only

Date: 2026-09-13

## Status

Accepted. Supersedes ADR 0010 (hybrid local-chat / Groq-browse).

## Context

Two things had drifted out of line with how we actually want to run Oli:

1. **ADR 0010 put chat/reasoning on a local Ollama** and kept the browser on Groq.
   That inverted the right split. Local hardware (8 GB RTX 3060) is best spent on the
   *one* workload that genuinely needs an open-source, self-hosted model —
   **computer-use / vision** (Fara-1.5, ADR 0016) — not on chat, where a strong hosted
   model is both better and cheap. In practice the `.env` had `CHAT_BASE_URL` pointed at
   `localhost:11434`, so when Ollama was asleep the intent classifier and every chat turn
   threw `APIConnectionError` — a single point of failure for the whole app.

2. **There was no failover for cloud calls.** Chat/intent/memory all hit a single
   endpoint; if Groq was down or rate-limited, the turn failed. We wanted Groq **or**
   Mistral, with automatic fallback and a clear message to the user.

## Decision

Split inference by **role**, not by tool, and make the cloud role resilient:

- **Cloud text role → an ordered provider chain with automatic failover.** Chat,
  reasoning, the fast tier, the intent classifier, and memory extraction all run over
  `settings.cloud_providers` (default **Groq → Mistral**). Each provider is
  OpenAI-compatible (endpoint + key + strong `model` + cheap `fast_model`). A new
  `oli.providers` module composes them with LangChain's `.with_fallbacks()`, so if the
  primary raises (down, rate-limited, bad auth) the next one answers. Only providers with
  a key are in the chain — no dead fallback. `LLMClient` (memory) walks the same chain.

- **Local model role → computer-use ONLY.** `browse` runs the native Fara-1.5 vision
  loop on a local Ollama in **both dev and production** (`BROWSE_ENGINE=fara` is now the
  default). It is the only local workload. As in ADR 0016 there is **no cloud fallback**:
  if the Fara host is unreachable the tool returns a clear "unavailable" message — now
  formatted with a clickable link to a recorded walkthrough (`FARA_UNAVAILABLE_URL`), so
  a viewer (e.g. a recruiter) can still see the workflow when the local box is offline.

- **The failover is visible.** When a non-primary provider answers, `run_turn` detects it
  (the answering model id maps to a fallback provider) and emits a one-time `notice`
  event — "The primary model was unavailable — answered via Mistral." When the *whole*
  chain is unreachable, the turn ends in a friendly error state rather than a raw
  traceback.

Mechanically: `config` gains `groq_fast_model`, a `mistral_*` block, and
`cloud_provider_order`, exposing `cloud_providers` / `primary_provider`. The old
`chat_*` and `browser_*` settings are gone — `chat_*` remain only as read-only aliases
of the primary provider — and the legacy browser-use cloud-LLM path in `tools/browse.py`
is retired (Fara is the sole engine).

## Consequences

- **No single point of failure for chat.** Groq down → Mistral answers; both down → a
  clear, actionable error. Chat no longer depends on a local box being awake.
- **Local GPU is spent where it's unique** — vision/computer-use — and nowhere else.
- **Plug-and-play providers.** Adding/removing a cloud provider is a keyed env block plus
  a name in `CLOUD_PROVIDER_ORDER`; no code change. Reordering changes the primary.
- **One less code path.** Dropping the browser-use cloud adapter (and its rate limiter)
  removes the `browser_*` config surface and simplifies `browse`.
- **Cost/latency of fallback:** Mistral is only hit when Groq fails, so steady-state cost
  is unchanged; a failover turn pays one wasted primary attempt before switching.
