# 10. Hybrid inference: local model for chat, Groq for browsing

Date: 2026-09-09
Status: Accepted

## Context

Oli ran entirely on Groq's hosted `gpt-oss-120b`. Groq's free tier allows ~30
requests/minute, and `browse` (browser-use) makes **one LLM call per step** — so a
single browse turn, stacked with the agent's own calls and background memory
extraction, blows past 30 rpm and hits `429 Too Many Requests` with 10–40s
back-offs, making browsing crawl.

The operator has a local GPU (RTX 3060 Ti, 8 GB) and Ollama with
`qwen2.5:7b-instruct` (tool-calling capable). 8 GB can run a 7–8B model well, but
**not** a 120B model — and browser-use needs a strong model to drive a browser
reliably, where small local models are noticeably weaker.

## Decision

Split inference by workload:

- **Chat / reasoning + memory extraction → a local model** (Ollama, OpenAI-compatible
  endpoint). These are frequent, latency-tolerant, and well within a 7B model's
  ability. They no longer consume any Groq quota.
- **`browse` (browser-use) → Groq's strong model**, where capability matters most —
  now with the whole 30 rpm budget to itself.
- **Rate-limit the browser's Groq calls** to a configurable ceiling (`BROWSER_MAX_RPM`,
  default 27) via an async token bucket, and lower `MAX_STEPS` (20 → 12), so browse
  paces itself under the limit instead of hitting 429s.

Mechanically, the single `groq_*` config was split into independent `chat_*` and
`browser_*` endpoint/key/model settings, each defaulting to `groq_*`. A pure-Groq
setup needs no new config; the hybrid just overrides `CHAT_*` to point at Ollama.
This is the provider-agnostic seam from ADR 0006 paying off — a config change, not
a rewrite.

## Consequences

- **No more 429s on the common path**: chat and memory leave Groq's budget entirely;
  browse is paced under the ceiling. Verified: chat calls hit `localhost:11434`.
- **Cost and privacy**: local inference is free and stays on the machine.
- **Quality trade-off**: the local 7B is weaker than 120B for hard reasoning, and
  the agent's browse still depends on Groq. Swapping models is one env change.
- **Two providers to run**: Ollama must be up for chat. If it's down, chat fails —
  an accepted single-user operational cost; falling back to Groq is just clearing
  the `CHAT_*` overrides.
- **Token-per-minute (TPM)** limits still exist on Groq; browse sends large payloads,
  so a TPM-aware budget in the limiter is a possible follow-up if TPM becomes the
  binding constraint.
