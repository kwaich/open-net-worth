# Acceptance criteria — Conversation rehydration parity

A conversation must render the SAME on reload as it did live. Today it does not: the
transcript stores only `role` + `content`, so everything the renderer needs beyond that is
either re-derived, joined from a 15-day log, or lost.

Measured 2026-08-21 against real Automation conversations on this instance:

- client `Message` carries **22** fields; `rehydrate_messages` returns **7**
- live renderer knows **10** block types; rehydrate emits **8**
- `usage` is stored and still unreachable — the row says `turn_index=1`, rehydrate computes
  `start_idx=0`, so the join misses and the legacy-zip fallback declines (`1 != 0`)

An Automation conversation hits every one of these, every time — nobody is watching while it
runs, so it is ALWAYS a rehydrated view.

---

### REHY-TIMESTAMP
- GIVEN an Automation run finished earlier and I open its conversation
- WHEN I look at any message in it
- THEN it shows the time that message actually happened — not blank, and not the time I
  opened the panel.

### REHY-COST-CHIP
- GIVEN an Automation run whose card reports a cost (e.g. `$7.24`)
- WHEN I open that run's conversation and reload the page
- THEN the turn's cost chip shows the same figure, without depending on a `cost_log` row
  that may have aged out.

### REHY-MENTION-CHIP
- GIVEN an older message that mentioned an entity with `@`
- WHEN I reload and view it
- THEN the mention renders as a CHIP with the entity's name — never as raw
  `[type: ref|label]` token text.

### REHY-ATTACHMENT
- GIVEN an older message that carried an attachment
- WHEN I reload and view it
- THEN the attachment renders as its file/thumbnail affordance — never as the raw
  attachment reference or its stub text.

### REHY-THINKING
- GIVEN an assistant turn that produced reasoning
- WHEN I reload the conversation
- THEN the reasoning is still available on that message, as it was live.

### REHY-FETCH-BLOCK
- GIVEN a turn that fetched a page (a `fetch` block rendered live)
- WHEN I reload
- THEN the fetch chip is still there. (Today `fetch` has zero occurrences anywhere in
  `backend/` — it is never rebuilt.)

### REHY-DUPLICATE-REVIEW
- GIVEN a turn that surfaced a duplicate-review card
- WHEN I reload
- THEN the card is still there. (Today `duplicate_review` is emitted only by
  `_post_execute_events`, the live SSE path.)

### REHY-NO-API-LEAK
- GIVEN messages now carry render metadata (`ts`, `usage`, …) alongside `role`/`content`
- WHEN any turn dispatches to a provider
- THEN the outbound request carries ONLY `role` and `content` per message. This is the
  load-bearing one: `_prepare_messages` copies with `{**msg, ...}`, so an unstripped extra
  key rides into the payload — the exact shape that produces a `400`.
