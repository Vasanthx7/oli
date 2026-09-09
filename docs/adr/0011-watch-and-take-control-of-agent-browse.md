# 11. Watch (and take control of) an autonomous browse

Date: 2026-09-09
Status: Accepted

## Context

ADR 0009 introduced the live browser (CDP screencast) and scoped it in slices:

- **A1 (shipped):** an interactive live browser the user drives themselves, which
  also provides in-UI login on a headless VM.
- **A2 (this ADR):** make an agent `browse` run *watchable* in the same view, with a
  "take control" pause so the user can step in (logins, CAPTCHA, an ambiguous step)
  and then hand back.

The autonomous `browse` tool (browser-use) ran a *separate*, unstreamed headless
Chromium, so the user only ever saw the final text result — never the run itself.

browser-use 0.13 drives Chromium directly over CDP (it dropped Playwright) and
exposes two things we need: a public `cdp_url` on `BrowserSession`, and synchronous
`Agent.pause()` / `Agent.resume()` that take effect at the next step boundary.

## Decision

**Tap the agent's existing browser — never launch a second one.** The `browse` tool
now constructs the `BrowserSession` itself (headless, sized to the live viewport,
optionally bound to a profile's user-data dir) and passes it to `Agent`. The live
session attaches to that browser's `cdp_url` via Playwright `connect_over_cdp`,
reusing the A1 screencast → subscribers → WebSocket plumbing unchanged.

- **Watch is read-only.** While attached in *agent mode*, UI input is dropped (both
  client- and server-side) so a stray mouse move can't fight the agent.
- **Take control** `pause()`s the agent and flips input on; **give back** `resume()`s
  it. State lives on the single `LiveSession` (single-user), coordinated across the
  SSE browse turn and separate `/api/live/control` · `/api/live/release` calls.
- **The frontend auto-surfaces the view** off the existing `browse` `tool_start` /
  `tool_end` SSE events — no new event type. A panel that popped open on its own and
  was never touched tucks itself away when the run ends.
- **Detach never closes browser-use's browser.** It stops our screencast and drops
  our CDP/Playwright connection only; browser-use owns the lifecycle and kills the
  browser right after the run.

We keep depending only on browser-use's *public* surface (`cdp_url`, `pause`,
`resume`, `Agent(browser_session=...)`), not its internal CDP session objects, since
that API has shifted across releases. Session construction degrades gracefully
(drops `viewport` on a `TypeError` retry) and attaching is best-effort: if a user is
already driving their own live browser, `attach_agent` no-ops and the run proceeds
unwatched.

## Consequences

- One browser per browse, so no extra memory pressure on a small VM — the added cost
  over an unwatched run is just JPEG encoding of frames that already render, dwarfed
  by the per-step LLM calls.
- The single-session constraint from ADR 0009 still holds: a user-driven live browser
  and a watched browse can't both be up at once; the second one degrades gracefully.
- Take-control pauses at a *step boundary*, not mid-action — the agent finishes its
  current step, then waits. Acceptable for logins/CAPTCHA, which happen between steps.
- Tab changes mid-run aren't followed: the screencast is bound to the page present at
  attach time. Good enough for single-tab flows; multi-tab following is future work.
- Full desktop "computer use" (ADR 0009's Level B) remains out of scope; this reuses
  the same screencast + input foundation and doesn't move that boundary.
