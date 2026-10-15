---
number: 2026-07-27
title: Drop the statement-import "auto-detect" account chip; always require an explicit account
status: Accepted
date: 2026-07-27
deciders: orchestrating agent (Claude) + repo owner (task directive)
---

# Drop the statement-import "auto-detect" account chip; always require an explicit account

## Context

`docs/adr/2026-07-26-statement-import-choose-read-verbatim-port.md` ported the handoff's `DropStep`
verbatim, then added a documented deviation (`PARITY_AND_GAPS.md` #6): a "✦ Detect from the
statement · auto" chip stands in for the account on the Choose step when none is picked yet, and
the CTA stays enabled — the prototype's own fixture always already knows the account, so it has no
state for "the server will try to detect it," but production's backend genuinely can (printed
last-4 digits, then remembered format profile — `services/statements/decisions.py::_suggest_account`,
acceptance SI-25, `tests/test_statement_account_detect.py`). That reasoning was sound on its own
terms and was reviewed (code-reviewer / ui-designer / qa-engineer all signed off per
`PARITY_AND_GAPS.md`'s verification ledger).

The owner reviewed the shipped screen and rejected it: the chip, the "Notes that describe what a
row really IS…" hint, and the two cache-hint variants read as unnecessary text/flow beyond what the
handoff shows, and asked for the auto-detect capability to be dropped from the UI entirely —
"always require the user to pick an account manually, matching the prototype's fixture behavior
exactly." This is a deliberate reversal of a previously-documented, reviewed decision, which is
exactly the class of change `CLAUDE.md` says gets an ADR (deviates from a stated invariant the
prior ADR + `PARITY_AND_GAPS.md` had established) rather than a silent diff.

The other extras raised in the same conversation (the ✦ honesty hint, the cache-hint text, the
"Prefer to map the columns yourself?" link) were kept — each maps to a real, load-bearing
production behavior the prototype's static fixture has no state for (instructions silently
reshaping a row, remembered-vs-changed instruction state, the live manual-mapping path) and the
owner did not ask for those to change. Only the auto-detect chip was in scope for removal.

## Decision

We will remove the "✦ Detect from the statement · auto" chip from the Choose step and require an
explicit account before a statement can be read, in every place that can trigger a server preview:

- The Choose step's "Read the statement" CTA is disabled whenever no account chip is picked
  (`disabled={!acctIds.length}` — previously `!acctIds.length && detectFailed`), matching the
  prototype's own `disabled={acctIds.length === 0}`.
- The Configure (manual column-mapping) step's two "Let Lucius do it instead" / "Import with
  Lucius instead" shortcuts — a second, previously un-gated path to the same server preview call —
  are now disabled until the step's own "Import into account" field is filled
  (`disabled={!accountId}`), closing a loophole where a user could reach a Lucius preview with no
  account chosen without ever seeing the Choose step's own gate.
- The frontend-only `detectFailed` and `detected` state, and the "✦ detected" badge on the Decide
  step's facts line, are deleted — they existed solely to manage the chip's offer/withdrawal and
  the post-preview "this was auto-detected" tell, both now unreachable since every frontend call
  site always sends a real `account_id`.

The backend detection capability itself (`_suggest_account`, `needs_account`, SI-25, and their
tests) is **not** removed — it is real, tested, reachable by any other caller that omits
`account_id` (REST/MCP/a future integration), and deleting it would be unrelated backend churn the
owner did not ask for. Only the frontend's use of it is retired.

## Consequences

### Positive
- The Choose step matches the handoff's own fixture behavior exactly: an account is always an
  explicit, visible user choice before Lucius reads anything.
- Removes a second, easy-to-miss way (the Configure step's shortcuts) to reach a server preview
  with no account chosen — that gap existed independently of the chip and would have survived a
  chip-only removal.
- Deletes three pieces of now-dead frontend state (`detectFailed`, `detected`, the "✦ detected"
  badge) rather than leaving them stubbed.

### Negative
- A statement whose printed account number would have resolved unambiguously (SI-25's layered
  detection) no longer saves the user that click — they always pick first, even when the backend
  could have gotten it right for free.
- The backend `needs_account` response the frontend used to react to is now unreachable through
  normal use; `runServerPreview`'s handling of it is defensive-only (kept for the case a
  concurrently-deleted account fails to resolve), which is a bit of code guarding an edge case that
  is very unlikely to fire.

### Follow-ups
- `frontend/design_handoffs/statement_import_handoff/PARITY_AND_GAPS.md` deviation #6 updated to
  record the reversal (not deleted — the doc's own convention is append-only history, not silent
  rewrite).
- `docs/qa/acceptance/statement-import.md` SI-90 rewritten for the no-chip/always-disabled-until-
  chosen behavior; SI-91 marked superseded (its premise — chip withdrawal on `needs_account` — no
  longer exists in the UI). SI-25 (the backend capability) is untouched aside from a note that the
  primary UI flow no longer triggers it.
- `ImportModal.chooseRead.test.tsx` updated: the auto-detect-by-default test now asserts the
  opposite (no chip, CTA disabled, "Select account" required); the two chip-removal tests' "falls
  back to auto-detect" assertions become "no chip renders, CTA disables again"; the
  `needs_account`-withdrawal test is removed (its premise is gone — the CTA can no longer be
  clicked with no account chosen in the first place).

## Alternatives considered
- **Keep the chip, trim only the explainer copy** — rejected: the owner's stated ask was to remove
  the auto-detect *flow*, not just shorten its copy.
- **Leave the Configure step's shortcuts un-gated** (chip-only removal) — rejected: it would have
  left a real, silent way to reach an auto-detected preview, defeating the stated goal of "always
  require the user to pick an account manually."
- **Also delete the backend `_suggest_account`/`needs_account` machinery** — rejected: out of
  scope (the ask was about the UI), it's independently tested (SI-25), and it may still serve a
  non-UI caller (REST/MCP) that doesn't have this modal's account-selection affordance at all.

## Agent provenance

- **Decided by:** orchestrating Claude agent, on the repo owner's explicit direction (via
  `AskUserQuestion`, choosing "Remove auto-detect chip entirely" over "trim copy only" / "leave
  as-is").
- **Lane / branch:** `main` (direct edit, no worktree lane — a contained, single-component change).
- **Alternatives weighed:** trim-copy-only vs. remove-chip-only vs. remove-chip-and-close-the-
  Configure-step-loophole vs. also delete the backend capability — see "Alternatives considered".
- **Driving artifacts:** `frontend/design_handoffs/statement_import_handoff/PARITY_AND_GAPS.md`
  deviation #6 (the decision being reversed); `docs/adr/2026-07-26-statement-import-choose-read-
  verbatim-port.md` (the port this amends); owner conversation asking why the Choose step shows
  "unnecessary text and even auto import flow" not in the handoff.
- **Why this way (one line):** the owner asked for the prototype's own fixture behavior — account
  always explicit, never guessed — and the fix has to close every path that could bypass that, not
  just the one the screenshot showed.
