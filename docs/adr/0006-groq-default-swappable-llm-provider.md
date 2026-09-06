# 6. Groq as the default, swappable LLM provider

Date: 2026-09-06
Status: Accepted

## Context

Oli needs an LLM for chat, tool-calling, browsing, and speech-to-text. Options
range from hosted APIs (Groq, OpenAI, DeepSeek) to self-hosted open weights. The
project runs on cheap infrastructure without a GPU.

## Decision

Default to **Groq** via its OpenAI-compatible API (`gpt-oss-120b` for chat,
Whisper for STT), configured entirely through typed settings. Because the client
speaks the OpenAI-compatible protocol with a configurable base URL, switching to
another provider (e.g. **DeepSeek**'s `deepseek-chat`) is an environment change,
not a code change.

## Consequences

- Fast, low-cost inference with no GPU to operate; the "brain" is rented per call.
- Provider is a configuration knob, avoiding lock-in and enabling A/B of models.
- Self-hosting open weights (for privacy) is possible later but needs GPU
  infrastructure; deferred, documented as a known trade-off.
