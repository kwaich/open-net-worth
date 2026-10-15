---
title: A harness-authored message is recognised by SHAPE, not only by its marker
status: Accepted
date: 2026-08-22
deciders: owner + implementing Claude agent
---

# A harness-authored message is recognised by SHAPE, not only by its marker

## Context

Owner report, 2026-08-22: *"the chat history doesn't look makes sense when alerts are
sent."*

Several things ride on a `role: "user"` transcript entry that no person typed: the
proactive-alert bridge (`ai/assistant.py::_PROACTIVE_TRIGGER`), the confirm/discard/deny
session notes (`connectors/chat/confirm.py::inject_session_note`), tool results, and the
turn-context block. The model must keep reading all of it. The *reader* must never be
shown it as the owner's own words, and it must never name their conversation.

`ai/tool_text.py::harness_note()` was introduced on 2026-08-21 to mark exactly this, and
`is_harness_note()` tests for the `«harness_note»` marker. Two gaps, both confirmed against
the owner's live Telegram session (`60fc4e06…`, 218 entries, 23 proactive injections):

1. **The marker post-dates the data.** 22 of those 23 proactive triggers are stored *bare* —
   written before the wrapper existed. `is_harness_note` returned False for every one, so
   they still rendered as the owner's own bubbles.
2. **`inject_session_note` never wrapped at all.** Every confirm/discard/deny on a chat
   channel wrote `[System: …]` as a plain user entry, so those rendered as the owner's words
   too — a live instance of the same defect, still being produced.

The visible consequence: the session's stored title is

    "[Automated monitor — the following message was delivered to"

`db/session_io.py::_recovered_title` names a conversation after its first user message, and a
harness note was it.

## Decision

**Recognise a harness-authored body by its shape when it carries no marker.**
`ai/tool_text.py` gains `_LEGACY_HARNESS_OPENINGS` — the full opening clause of each
harness-authored body that predates the wrapper — and `is_harness_note` returns True for a
marker *or* a legacy opening.

Matched on a **distinctive full opening clause**, never a loose keyword or a bare
`[System: ` prefix. The failure mode of over-matching is silencing something a person
actually typed, which is worse than the bug being fixed; "tell me about the automated
monitor you mentioned" and "[System: ...] is a weird thing to say" both stay the owner's
words, and are pinned as tests.

Forward, the marker remains the mechanism: `inject_session_note` now wraps. The legacy list
is a closed set describing data already on disk, not a second write format.

### The `db/` copy is duplicated on purpose, and pinned

`db` may not import `ai` (import-linter contract 1), and `session_io._recovered_title` needs
the same predicate. It carries its own copy — the convention that module already documents.
A copy that drifts is not hypothetical: it is what happened during this very change (the
`[System: …]` openings were added to `ai/tool_text.py` and not to the copy, and the title
repair then landed on *another* harness note). `tests/test_proactive_alert_history.py`
asserts the two lists are equal **and** that the two predicates agree on a probe set.

### Related, same change

- **An alert keeps the time it was SENT.** `_proactive_pair` discarded each event's
  `created_at` and let `_stamp_message_times` fill in the injection time, so an alert pushed
  six hours earlier landed in history at the moment the user next replied (live evidence:
  entries 206–209 all stamped `21:46:59.303`–`.306`). The pair is now stamped from the latest
  admitted event, and each rendered line carries its own `[sent …]` label — necessary because
  coalescing collapses several alerts into one block, where a single stamp would say nothing
  about any individual one.
- **`_recovered_title` reads real transcripts.** It only ever looked at `str` content, but
  real user content is a *block list*, so on an actual conversation it fell through every
  message to the generic label. It now reads text blocks, skips any entry containing a
  `tool_result` block (the harness replying to itself), and stops at `<turn_context>`.

## Consequences

- Existing transcripts are repaired **at read time** — no data migration for the 22 rows.
- A stored *title* is not read-time derived, so it needs a one-off repair:
  `backend/scripts/retitle_harness_note_sessions.py` (dry-run by default, behind the standard
  `_live_data_guard`). It re-derives the title the fixed `_recovered_title` produces and
  touches nothing else. On the owner's data it recovers the genuine first message —
  *"What is your model? And who are you?"* — and is idempotent.
- Adding a new harness-authored body means wrapping it with `harness_note()`. It does **not**
  mean extending the legacy list; that list only ever describes bodies already written.

---

## Amendment, 2026-08-23 — the per-event cap is removed

Owner: *"let's not cap 600 char please."*

`MAX_PROACTIVE_SUMMARY_CHARS = 600` truncated every alert at RECORD time. Measured against
the owner's live queue: **12 of 15 staged alerts hit it**, routinely cutting mid-URL. Half a
sentence is worse context than a long alert, and the cap bought nothing the block budget did
not already provide — `MAX_PROACTIVE_BLOCK_CHARS` is the real ceiling on what reaches durable
history; a second cap only decided how that same budget was divided.

**Removing it alone would have made things worse**, which is the part worth recording. The
admission loop skipped any line that did not fit (`omitted += 1; continue`), so uncapped
alerts would have been DROPPED wholesale rather than shortened — measured at **3 of 15
admitted** at realistic full length, against 14 before. Three changes were therefore needed
together:

1. record the alert whole (no record-time truncation);
2. raise `MAX_PROACTIVE_BLOCK_CHARS` 8,000 → 40,000;
3. **shorten the last admitted line to fit instead of skipping it**, with an explicit
   `…[truncated to fit this turn's alert budget]` marker — a dropped alert is one the reader
   cannot ask about, because they were never shown it. Below `MIN_ADMITTED_LINE_CHARS` (400)
   of remaining budget a stub is noise, so the group is reported as omitted instead.

The original scar is unchanged: a burst is still bounded (the incident was 367 report bodies
joined into one permanent history entry) and overflow is still reported honestly. The ceiling
is 5× larger, not absent.

Verified against the owner's real queue: 15/15 admitted at 8,225 chars as stored, and 15/15 at
36,725 chars at realistic full length.

`tests/test_proactive_context_bounding.py::test_single_oversized_message_is_bounded_at_record_time_not_injection_time`
pinned the removed behaviour by name. It was rewritten (`…_at_INJECTION_time`) to assert the
invariant that actually survives — one pathological message can never blow the per-turn
budget — at the seam that now enforces it.

**Known residual:** ~17% of the already-truncated text was source URLs (Google News redirect
links, hundreds of chars each) carrying near-zero value for the model. At full length that
share grows. Shortening or stripping those would reclaim more budget than any cap without
losing a word of analysis — not done here.

---

## Amendment, 2026-08-24 — the group COUNT cap is removed; selection is by recency

Live failure. 36 alerts were staged. `_proactive_pair` admitted `coalesced[:20]` — the
first 20 in ARRIVAL order — and dropped the 16 newest, among them a *"Mail scan — 4
transactions logged"* sent minutes earlier. The owner then asked *"the 3rd one was for
Thanh Lam Diva Concert ticket"*, and Lucius answered that it could find no such
transaction, because it had never been shown that message. Replying to the Telegram
message directly is what rescued it — the reply-quote carried the text in.

Evidence from the live transcript, entry [38]:

    alert lines: 20 | earliest 2026-08-22 21:06 UTC | latest 2026-08-23 10:54 UTC
    block chars: 20,174   (budget 40,000)
    [16 additional proactive update(s) omitted here to save context …]
    'Mail scan' inside the block? False

Two defects, both introduced by the 2026-08-23 amendment above:

1. **Oldest-first admission.** If anything must be dropped it has to be the stale end —
   a user asks about what just arrived, not about yesterday.
2. **A count cap that no longer tracked the char budget.** Raising
   `MAX_PROACTIVE_BLOCK_CHARS` 8,000 → 40,000 left `MAX_PROACTIVE_EVENTS_ADMITTED` at 20,
   a number chosen for the old budget. The count fired with half the budget unused.

**Decision.** `MAX_PROACTIVE_EVENTS_ADMITTED` is DELETED. `MAX_PROACTIVE_BLOCK_CHARS` is
the single limit. Groups are SELECTED newest-first until that budget is reached, then
RENDERED oldest-first so the block still reads as history. Only the newest overflowing
group is shortened-to-fit (below `MIN_ADMITTED_LINE_CHARS` a stub says nothing the
omission count does not); anything genuinely dropped is still reported by count.

A single `_proactive_line()` now renders the line for BOTH the sizing pass and the render
pass — measuring one string and emitting a different one is how a budget silently
overruns.

### Why alerts are staged at all — the question this raised

The owner asked the right question: *"why don't we just include the last messages as
normal conversations, including the alerts?"* The honest answer is that the deferral is
required and the summarising is not. A session's whole transcript is ONE JSON column that
a running turn holds in memory and rewrites wholesale (`SessionStore.set_transcript`), so
an alert appended from a scheduler thread mid-turn would be clobbered — hence the sidecar
and the inject-at-next-turn design. The coalescing and capping on top of it came from one
incident (367 report bodies becoming a single permanent history entry) and hardened into a
general rule. Coalescing is lossless and earns its place; a lossy count cap did not.
Treating alerts as ordinary messages, with the fold window and age-decay deciding what to
carry, remains the better end state and needs an append-only message store rather than a
per-session JSON blob.

Verified against the owner's live queue: 27/27 admitted at 24,311 chars, nothing omitted;
and a replay of the exact 36-alert failure now admits the Mail scan.
