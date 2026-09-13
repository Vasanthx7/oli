# End-to-end browse cases — failure analysis & handling

15 end-to-end scenarios that push the browse harness toward the product goal: **hand a
task back to the user when needed, and complete complex, multi-step, logged-in flows.**
They live in `scenarios.py` as `E2E_SCENARIOS` and run through `run_vendored.py`.

Profiles used (three saved logins in the account): **`amazon`**, **`cult`** (cult.fit),
**`zomato`**. Flows that book/order/submit or need a live login are marked `manual` — the
runner refuses them without `--allow-manual`, so nobody places a real order by accident.

```bash
# safe, logged-out handover/robustness cases:
uv run python -m evals.browse.run_vendored --ids e9-newsletter-missing-info,e13-ddg-bot-block-graceful
# an authenticated flow, watched, with 9B routing (real side effects gated):
uv run python -m evals.browse.run_vendored --ids e3-amazon-add-cable-stop --autoroute --allow-manual
```

The "handle" column references the harness takeaways in
[`AB_VENDORED_VS_OFFICIAL.md`](AB_VENDORED_VS_OFFICIAL.md): **#1** resumable
`ask_user_question` handover (**✅ implemented — ADR 0018**), **#2** critical-point
confirmation, **#3** per-turn checkpoint/resume, **#4** `facts` working-memory
re-injection, **#5** text-observation fallback for dense/forms, **#6** action validation.
"done" = already in `fara_browse.py`.

> **Status:** #1 (resumable handover) is now live — a browse that hits an
> `ask_user_question` pauses with the browser open and resumes on the user's next reply
> (`has_pending_browse()` / `resume_pending_browse`). Cases e5/e9/e10/e11/e12 now complete
> the ask→answer→continue loop instead of dead-ending. #3 (survive a restart) is handled
> as a **warm relaunch**: the handover is persisted to disk, so after a restart the reply
> re-launches a fresh browse seeded with the original goal + answer + last URL (ADR 0018).

## The 15 cases

### Authenticated reads

**e1 · Amazon cart subtotal** — `amazon`, read, dense
*Why it fails:* (a) the profile never reaches the loop → the run browses logged-out and
sees an empty/guest cart; (b) cookies expired → a login wall the agent can't pass; (c) 4B
mis-reads the small subtotal on a cluttered cart page.
*Handle:* profile auto-resolve from the goal (**done**); on a login wall, a resumable
inline-login **handover (#1)**; route dense/commerce pages to **9B** (**done**, autoroute).

**e2 · Cult.fit my classes this week** — `cult`, read, dynamic
*Why it fails:* cookies expired (login wall); or the week calendar renders async and the
screenshot is captured mid-render (blank), so the agent "reads" nothing.
*Handle:* login **handover (#1)**; screenshot-retry-through-navigation (**done**) plus a
short settle wait; **text-observation fallback (#5)** if the calendar is canvas/JS-heavy.

### Authenticated interaction with a critical-point stop

**e3 · Amazon add cheapest USB-C cable, stop before order** — `amazon`, interaction,
critical-point, dense
*Why it fails:* 4B lands *near*, not *on*, the small "Add to cart" control on a dense
listing (the documented 4B grounding gap); or the agent proceeds toward checkout instead
of stopping.
*Handle:* autoroute → **9B** for commerce/interaction (**done**); **critical-point confirm
(#2)** before the irreversible click (the prompt already instructs this) + **handover (#1)**
so the user authorizes the finish; purchase guardrail as the hard backstop (**done**).

**e4 · Zomato reorder last order, stop before payment** — `zomato`, interaction,
critical-point, dense
*Why it fails:* address/slot re-selection interrupts the reorder path; the food-app UI is
dense/bot-heavy for 4B; or the agent walks into the pay step.
*Handle:* **9B** routing; **critical-point confirm (#2)** + **handover (#1)**; purchase
guardrail backstop (**done**).

**e5 · Cult.fit book a yoga class "near me" tomorrow, confirm first** — `cult`, handover,
ambiguous, critical-point
*Why it fails:* **two** critical points at once — "near me" is ambiguous (which center?)
and booking is irreversible + unauthorized. Today our loop **dead-ends** `ask_user_question`
(returns "I need input…" and stops), so the clarification can't be answered and the flow
can't resume.
*Handle:* **resumable handover (#1)** — the single highest-value gap — plus
**critical-point confirm (#2)** before booking.

### Multi-hop / complex reasoning

**e6 · Amazon reorder protein bars (Your Orders → buy again), stop before order** —
`amazon`, multi-hop, memory
*Why it fails:* deep navigation (Orders → item → buy-again → cart); the agent loses the
target item across pages once screenshots are trimmed to the most-recent 3.
*Handle:* **`facts` re-injection (#4)** as working memory + per-turn **checkpoint/resume
(#3)** so a long flow survives an interruption.

**e7 · Compare a book's price across two sites** — multi-hop, memory, reasoning
*Why it fails:* the model reads price A, but after its screenshot is trimmed it "compares"
against a hallucinated number — the canonical working-memory failure.
*Handle:* `pause_and_memorize_fact` + **re-inject facts (#4)** so price A survives to the
comparison step.

**e8 · Cheapest 3 books in a category with prices** — interaction, extract, ordering
*Why it fails:* the agent reads the default listing order instead of scanning/sorting by
price, or stops after one item.
*Handle:* stronger extraction hint; prefer `read_page_answer_question` over page text for
the list rather than pixel-reading each price.

### Handover / critical points (logged-out — safe to run)

**e9 · "Sign me up for the newsletter"** (no email/site given) — handover, missing-info
*Why it fails:* Critical point Case 1 (missing info). Correct run asks for the email;
today the ask is terminal so it can't continue when the user replies.
*Handle:* **resumable handover (#1)**.

**e10 · "Book a table for dinner"** (no restaurant/time/party) — handover, ambiguous
*Why it fails:* Critical point Case 2 (ambiguity). Grades the *question*, not a booking.
*Handle:* **resumable handover (#1)** (prompt already asks).

**e11 · Fill a form, then ask before submitting** — handover, form, critical-point
*Why it fails:* Case 3 (irreversible, unauthorized) *and* the 4B multi-field-form weakness
(radios/checkboxes) — doubly hard. Must fill correctly yet gate the submit.
*Handle:* **9B** + **text-observation fallback (#5)** for the fields; **critical-point
confirm (#2)** + **handover (#1)** before submit.

**e12 · Open my cult.fit account, show membership** (may hit a login wall) — `cult`,
handover, login
*Why it fails:* if the profile isn't authenticated, browse today returns a "set up a
profile" message and stops.
*Handle:* **inline-login handover** (partly built) — open the site in the live view, let
the user sign in, then **resume the original goal (#1/#3)**.

### Robustness / graceful failure

**e13 · DuckDuckGo search, open first result** — robustness, bot-block, search
*Why it fails:* DDG's HTML endpoint bot-blocks the headless browser (one of the two A/B
failures). The agent loops or reports a wrong answer.
*Handle:* **detect the block** and fall back to another engine (`web_search` → Bing) or
report it cleanly — don't loop (graceful report partly **done**).

**e14 · Hacker News #1 story title + points** — dynamic, extract
*Why it fails:* the agent reads a lower-ranked item, or reports a stale rank after the page
shifts; small metadata (points) is easy to mis-read by pixels.
*Handle:* `read_page_answer_question` over page text for rank + points.

**e15 · Authorized form submit (anti-dead-loop guard)** — form, robustness,
authorized-submit
*Why it fails:* 4B stalls on the form — repeat-clicking / scrolling to hunt the submit
button — and burns the step budget (`incomplete-max-rounds`).
*Handle:* action-aware **stall nudges (done)** must break the loop; **9B** +
**text-observation fallback (#5)** is the accuracy lever. This is the regression guard
that the stall detector keeps working.

## How these map to work

| Harness change | Unblocks cases |
|---|---|
| #1 Resumable `ask_user_question` handover | e5, e9, e10, e11, e12 (+ every auth flow that hits a login wall) |
| #2 Critical-point confirm (vs hard refuse) | e3, e4, e5, e11 |
| #3 Per-turn checkpoint/resume | e6, e12 (long/interrupted flows) |
| #4 `facts` working-memory re-injection | e6, e7 |
| #5 Text-observation fallback (dense/forms) | e2, e8, e11, e14, e15 |
| #6 Action validation / clean terminate | robustness across all |
| 9B autoroute (done) | e3, e4, e6, e11, e15 |

**Reading of the set:** the biggest single unlock is **#1 (resumable handover)** — it is
required by a third of the cases and is the difference between "asks and gives up" and
"asks, gets the answer, and finishes." After that, **#4 (working memory)** and **#5
(text-observation fallback)** clear the multi-hop and dense-form classes respectively.
None of these require abandoning the vendored loop — they are additive.
