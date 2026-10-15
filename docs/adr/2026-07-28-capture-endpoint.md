---
title: External clients describe a spend in words; POST /api/capture lets Lucius decide and log it
status: Accepted
date: 2026-07-28
deciders: repo owner (direction + both trust calls), implementing Claude agent
---

# External clients describe a spend in words; `POST /api/capture` lets Lucius decide and log it

## Context

The iOS tap-to-log shortcuts first shipped against `POST /api/transactions`. That worked, but it
put the ledger's shape inside the client: the shortcut had to split `amount|payee|card`, know that
accounts resolve by exact name, know which fields exist (`external_refs` not `external_id`, `memo`
not `note`), and know not to send `from_currency`. Twenty-eight actions and four install questions,
and every one of those facts was a thing that could drift out of sync with the server.

The owner's call: *"We shouldn't use the POST transaction in the first place — create a new
endpoint, throw those contents at Lucius and let the AI decide and log. It's much more flexible."*

That is the right cut. The knowledge belongs on the server, and the assistant already exists to
turn a sentence into a booked row — it does exactly this every day over Telegram.

## Decision

`POST /api/capture` takes `{text, source, ref}` and runs **one Lucius turn** over the text with the
ledger tools available. Clients describe what happened; the server decides what it means.

`ai/capture.py` owns the turn (the router stays thin per the layering rule; `api → ai` is the
permitted direction, and this is the same shape `api/chat.py` already uses). Three properties
separate it from an interactive turn:

- **`set_unattended(True)`** — a statement of fact about the caller. The hardline tier reads it, so
  `delete_transactions_where` and `apply_payee_rules` refuse outright. This matters more here than
  for a scheduled task: the text reaching the model is written by third parties (merchant and card
  names), so the one class of operation whose selection cannot be verified afterwards should not be
  reachable from it at all.
- **Surface `capture:<source>`** — deliberately neither `web` nor `connector*`, so
  `_confirm_gate.can_render_card` returns False and no card is staged. On a surface with nobody
  watching, a staged card is a write nobody can ever apply.
- **A disposable session**, cleared in `finally` — fifty taps a week must not become fifty entries
  in the session list, nor an unboundedly growing history.

The captured text is fenced in `<captured_text>` and explicitly labelled as data, not instructions.

### Trust posture — writes apply immediately, with no envelope

**The owner chose this explicitly**, over the two alternatives offered (propose-then-confirm via
Telegram, or write inside a pre-authorized envelope).

This deviates from the recommendation in `2026-07-19-confirm-gate-surface-matrix`, which argues
unattended surfaces should write only inside a declared envelope. Three things make it defensible,
and one thing keeps it honest:

1. It is **not a regression**. The `POST /api/transactions` path this replaces already wrote
   unattended with no gate whatsoever. Nothing became more permissive.
2. `evaluate_envelope(None, …)` already returns ALLOWED — that fail-closed reading was
   deliberately reversed on 2026-07-20 after it silently disabled the owner's primary email-to-
   ledger automation. So "no envelope" is the system's existing default, not a bypass.
3. There is no UI for declaring an envelope, so requiring one would gate the feature behind a
   policy no user can currently express.
4. The hardline tier still applies. "No envelope" narrows to "routine writes proceed"; it does not
   mean "anything goes".

**The residual risk, stated plainly:** an authenticated caller can put arbitrary text in front of a
tool-enabled model that holds the owner's authority. The hardline set bounds the blast radius to
routine writes, and routine writes are individually reversible, but a crafted merchant name that
talks Lucius into booking something wrong is not structurally prevented — only made unattractive
by the fencing and the narrow prompt. Revisit if an envelope UI ever lands.

### Synchronous, because the caller needs an answer

Also the owner's call. The endpoint runs the turn and returns Lucius's own one-line summary, which
the shortcut shows as the notification. The alternative (202 + `notify()`) is snappier but can only
say "sent", and surfaces failures somewhere other than the phone that caused them.

The cost is a request that takes seconds — invisible for a background automation, but it makes a
client timeout *after* a successful write a realistic event, which is why idempotency is not
optional here.

### Idempotency is checked, not assumed

`ref` (e.g. `tap:<ISO timestamp>`) is checked against existing `external_refs` **before** the turn
runs; a replay returns `duplicate: true` having spent nothing.

For that check to fire on a retry, the ref has to have landed on the row — and the write happens
inside a tool call the server does not post-process, so the model is *asked* to stamp it, not
forced. Rather than assume, the response reports `idempotent`, read back from the store after the
turn. A `false` there means a retry of that capture would double-book. Making this deterministic
needs either a capture-level store of processed refs or a post-turn stamping pass; both were judged
heavier than the honest signal, and neither is hard to add later.

## Consequences

### Positive
- The shortcut drops from 28 actions to 15 and from 4 install questions to 2 (URL + token). No
  account mapping, so no "one copy per card".
- One endpoint serves any client that can describe a purchase — the Android Tasker recipe becomes
  "forward the notification text" rather than "parse the notification text".
- The notification now reports what the *server* booked, including the category the phone never
  knew about.

### Negative
- Every capture costs model tokens, which a plain REST POST did not. Bounded by a 20/min limiter
  and a 2000-character cap, both enforced before the turn starts.
- Latency goes from milliseconds to seconds.
- The outcome is no longer deterministic: the same sentence can book differently as the model or
  the account set changes. For a capture path whose input is already fuzzy this is the trade the
  owner chose, but it is a real property change, and it is why the reply is surfaced verbatim
  rather than reduced to a success chip.

## Agent provenance

- **Decided by:** the repo owner (the architecture call and both trust decisions), implemented by
  the Claude agent on this lane.
- **Lane / branch:** `claude/mobile-shortcuts-copy-reinstall-0bvd91`.
- **Alternatives weighed:** keep `POST /api/transactions` with a smarter client; reuse
  `POST /api/chat` (heavy — sessions, mentions, attachments, a pending-confirm flow, and a response
  shape a Shortcut cannot parse); a purpose-built capture endpoint (chosen). On trust:
  propose-then-confirm vs. envelope-bounded vs. immediate write (chosen). On timing: 202 + notify
  vs. synchronous (chosen).
- **Driving artifacts:** `docs/prd/tap-to-log-transactions.md`; `backend/ai/capture.py`;
  `backend/api/capture.py`; `tests/test_capture_api.py`;
  `docs/adr/2026-07-19-confirm-gate-surface-matrix.md`.
- **Why this way (one line):** the server already knows how to turn a sentence into a ledger row —
  making every client relearn that was the actual bug.

## Amendment — 2026-09-23: synchronous up to a bound, then background

Owner direction: *"make the capture endpoint process in background … in case the response takes
too long, because Shortcuts iOS has a timeout."* A slow provider made the synchronous request
outlive the Shortcut's timeout, so the phone reported a failure for a capture that may still land.

The turn now runs in a server-owned task (`ai.capture.start_capture`); the request waits on it,
shielded, for up to 50 s (`wait` field, clamped 0–50 — owner's call, just under the ~60 s
Shortcuts timeout that is reported but not documented). A turn that finishes in time answers exactly
as before. One that doesn't answers `202 {pending: true}` with a "still logging" `reply`, keeps
running, and delivers its outcome through `services.notify` — in-app + Telegram only, by the
owner's call; email and webhook channels are left for alerts — so the synchronous answer the
original decision valued is kept for the common case, and 202 + notify covers only the tail. A
retry carrying a `ref` that is still in flight joins the running task (and skips the rate limit)
instead of starting a second booking turn, closing the gap `already_captured` cannot see: a write
that has not landed yet.

`run_capture` sets its cost surface and unattended flag before its first `await`, which is why a
plain task is safe here without routing through `ai/turns.py` (a capture has no live session to
attach to). A capture still in flight at process shutdown is cancelled with the loop.
