# A local mock OpenAI-compatible server proves live-turn UI, no paid provider required

- **Date**: 2026-08-06
- **Status**: Accepted
- **Area**: QA infrastructure (`frontend/qa/mock-llm-server.mjs`,
  `frontend/qa/concurrent-conversations-live-turn.mjs`) — no production code touched.

## Decision

We will drive genuinely-live-turn acceptance criteria (concurrent-conversations E1/E2/E4 — the
`working` indicator, which exists ONLY in `backend.ai.live_sessions`'s in-process registry while a
turn genuinely executes, with no file/IndexedDB backing to seed) with a tiny, deterministic,
in-repo OpenAI-compatible SSE mock server (`frontend/qa/mock-llm-server.mjs`), reached via the
app's own `openai_compatible` provider transport with a caller-supplied `base_url`
(`backend/db/model_config_store.py`'s `"needs_base_url": True` entries). A QA script
(`frontend/qa/concurrent-conversations-live-turn.mjs`, `make qa-cc-live-turn`) writes
`model_config.json` directly into a disposable, self-booted `DATA_DIR` to point the instance at
the mock, then drives real turns through the real UI with Playwright.

The mock speaks just enough of the streaming Chat Completions wire format to exercise a real turn
through `backend.ai.turns.spawn -> assistant.run`'s tool loop: a slow, word-by-word "thinking"
round, one real tool call (`get_portfolio`, read-only, no confirm gate), then either a slow final
answer or — when the driving message carries a `FAIL_MARKER` — an HTTP 500 instead of a stream, so
the `openai` SDK raises and the turn ends in `turns._drive`'s `except Exception` branch exactly
like a real provider outage would.

## Why

The Anthropic account was over its configured usage limit until 2026-09-01 — `make qa-e2e`
(browser-use, LLM-driven) could not take a single step, and the acceptance file's own record
(`docs/qa/acceptance/concurrent-conversations.md`) already documents two prior attempts dying in
seconds against that limit. The owner's brief was explicit that faking the `working` signal by
stubbing the network response (fabricating a turn that never ran) would misrepresent what the UI
does — not acceptable evidence. A real local provider-shaped endpoint sidesteps both problems at
once: the app's own `openai` SDK client genuinely round-trips HTTP to it, `backend.ai.turns`
genuinely spawns and tracks a real `asyncio.Task`, and `live_sessions` genuinely opens and closes
a real episode — only the *token source* is synthetic, not the mechanism being proven. Zero
network egress (127.0.0.1 only), zero cost, fully deterministic and re-runnable.

## What this technique caught that reading the code alone would not have

Running the real thing surfaced a genuine product gap that a purely-theoretical read of
`useConversations.ts` made plausible but did not prove: **E4's mobile badge does not update live**.
`useConversations()` fetches `GET /api/sessions` exactly once, in a mount-only effect with an
empty dependency array — there is no periodic refetch and no bus event that wakes it when a
background turn this browser never locally touched finishes. The spinner (`streamingIds`, fed by
the polled `GET /api/sessions/live`) genuinely clears live; the badge (`hasBadge`, which needs a
FRESH `conversations` fetch to see the durable `last_turn_status`) does not, until the next reload.

A first pass at proving this produced a **false PASS** for the live badge: running the E4 watcher
concurrently with the fail/success scenarios' own watchers left THEIR already-completed
conversations sitting in the very first `GET /api/sessions` snapshot E4's watcher mounted against
— `hasBadge` in `frontend/src/components/mobile/MobileTabBar.tsx` is a union over EVERY
conversation the browser knows about, not scoped to the one conversation the scenario cares about.
Caught by dumping the matched button's `outerHTML` at the moment the check "passed": the spinner
was still rendering in the same snapshot, meaning the turn this scenario cared about had not even
finished. Fixed by running E4 first, in isolation, before any other scenario's conversation has a
chance to complete — see `watchMobileE4`'s and `runE4Scenario`'s docstrings in the script itself.
This is exactly the trust-but-verify case the task brief warned about: an assertion read from the
component alone (`MobileTabBar.test.tsx`'s own mocked-`statusFor` unit test already "proved" the
badge renders given the right status) is not the same claim as "the real data layer ever produces
that status live" — only driving the real app end to end distinguished the two.

## Consequences

### Positive
- E1 (live indicator, cross-tab, cross-width, sidebar AND the full conversation browser) and E2
  (clears live, no reload, on BOTH the success and error branch) are now proven against the real
  running app rather than left as a permanently-blocked gap behind a usage limit.
- A reusable pattern for the next provider outage or a fully offline/CI environment: any future
  "must be a real turn" acceptance criterion can point at this same mock rather than requiring a
  live model call.
- The false-positive catch above is itself evidence the technique works as intended — it found a
  real bug in the TEST'S isolation, not just in the product.

### Negative
- The mock is deliberately narrow (one scripted scenario shape: a thinking round, one tool call,
  then success or failure) — it is not a general-purpose LLM stand-in and would need extending for
  a criterion that needs a different turn shape (multiple tool rounds, an approval-gated tool,
  streamed `thinking_delta`, etc.).
- E4's badge half remains **unproven as literally specified** ("no reload") — this ADR documents a
  real, confirmed product gap rather than closing it, per the task's explicit instruction not to
  modify product code to make evidence easier to capture. Captured to the Hardening Board inbox
  (`make bug`) as a follow-up.

### Follow-ups
- Consider giving `useConversations()` a bus-driven or interval refetch so a background
  conversation's completion is visible without a reload — would close E4 for real, but is a
  product change, out of scope here.
- `qa/browser-use/runner.py` is still hard-wired to `ChatAnthropic` (noted independently in the
  acceptance file's own "still open" section) — this ADR's mock is a narrower, purpose-built
  alternative for the specific criteria that need a genuinely live turn; it does not replace the
  broader multi-provider need for the LLM-driven browser-use harness itself.

## Alternatives considered

- **Stub `GET /api/sessions/live`'s response to fabricate a "live" session id.** Rejected — the
  task brief calls this out by name as the kind of fake that misrepresents what the UI does: no
  turn would actually be running, no `asyncio.Task` would exist, and `assistant.upsert_session_meta`
  would never be exercised. Indistinguishable from a lie to anyone reading the evidence later.
- **Wait for the usage limit to reset (2026-09-01) and run `make qa-e2e` for real.** Rejected —
  would leave E1/E2/E4 unproven for weeks with no way to know today whether the underlying
  behavior (including the E4 gap this technique found) is even correct.
- **Point the QA instance at a real alternate free-tier provider (e.g., a local Ollama model).**
  Rejected — non-deterministic output, real installation/model-pull dependency for anyone
  re-running this, and no ability to script a DELIBERATE failure on demand (needed for E2's error
  branch) without still faking something.

## Agent provenance

- **Decided by:** senior-engineer agent, working the final "close E1/E2/E4" task on the
  concurrent-conversations feature.
- **Lane / branch:** `feature/settings-redesign` worktree
  (`.claude/worktrees/agent-a429842dca125fb84`), STEP-0 merged with `main` first.
- **Alternatives weighed:** stubbing the live-sessions response (rejected as the exact kind of
  fake the brief warns against); waiting out the usage limit; pointing at a real local provider —
  see above.
- **Driving artifacts:** `docs/qa/acceptance/concurrent-conversations.md` (the "Remaining gap: E1,
  E2, E4 only" note this closes); the task brief's own explicit mock-server proposal.
- **Why this way (one line):** a real HTTP round trip through the app's own provider transport
  proves the real mechanism end to end, at zero cost and zero risk, and — empirically, not just in
  theory — is what caught a genuine product gap a code read alone would have missed.
