# 9. Live browser (human-in-the-loop) via CDP screencast

Date: 2026-09-09
Status: Accepted

## Context

Oli can browse the web autonomously (`browse`, browser-use) and can reuse a
human-authenticated session via browser profiles (ADR-less feature). Two gaps
remained:

1. The one-time **login** for a profile needed a *visible* browser, so it only
   worked on a machine with a display — not on a headless VM.
2. To move toward an OpenAI-Operator-style experience ("Level A"), the user needs
   to **watch** the browser and **take control** at any moment (logins, CAPTCHA,
   ambiguous steps).

Both require streaming a live browser into the web UI and sending the user's
mouse/keyboard back to it. The obvious approach is a **noVNC** stack (Xvfb +
x11vnc + websockify) inside the container. A lighter alternative is the Chrome
DevTools Protocol's built-in **screencast** (`Page.startScreencast` emits JPEG
frames; `Input.*` injects events), which Playwright exposes directly.

## Decision

Use **CDP screencast**, not noVNC, for the live browser view.

- A headless Chromium is streamed to the frontend as JPEG frames over a WebSocket
  (`/api/live/ws`); the frontend renders them to a `<canvas>` and forwards mouse
  and keyboard events back.
- It runs **fully headless**, so it behaves identically on a laptop and a headless
  VM — no virtual display or VNC server required.
- Bound to a profile, the live session uses a Playwright *persistent context*, so a
  human login is saved to the profile's user-data dir for the agent to reuse.
  Credentials never reach the LLM (consistent with the profiles feature).

This is delivered in two slices:

- **A1 (this ADR):** an interactive live browser the user drives themselves —
  which also provides in-UI login on a headless VM.
- **A2 (shipped — see ADR 0011):** make an agent `browse` run watchable in the same
  view with a "take control" pause. **B (future scope):** full computer use (a virtual
  desktop + a vision/GUI-grounding model), which would need a model beyond the current
  text-only Groq default — see ADR 0006's swappable-provider seam.

## Consequences

- Far less infrastructure than noVNC: no Xvfb/x11vnc/websockify, nothing extra in
  the image; the browser we already ship *is* the streaming engine.
- Headless-native and locally testable — verified with real Chromium in tests and
  through the live WebSocket, without Docker.
- Screencast only emits frames on visual change, so the newest frame is cached and
  replayed to late-joining viewers (otherwise a static page shows blank).
- Single-user assumption: one live session at a time. A profile bound to a live
  session can't simultaneously be used by `browse` (same user-data dir) — an
  accepted constraint for now.
- Full desktop "computer use" (Level B) is intentionally out of scope here; the
  screencast + input foundation generalises to it later.
