---
title: An incomplete statement parse withholds the ledger diff, never runs it partial
status: Accepted
date: 2026-09-09
deciders: implementing agent (lane/B16-parse-completeness-gate) + repo owner
---

# 2026-09-09. An incomplete statement parse withholds the ledger diff

## Context

Real incident, 2026-09-08: a 5-page scanned card statement went through `tier3_vision`
(`backend/services/statements/tiers.py`). Page 4's reply hit the 8192-token output cap
(`_ROW_MAX_TOKENS` in `_row_complete`) because the owner's endpoint rejected the
reasoning-disable param (`effort="none"`) and the model spent ~23k characters reasoning before
the JSON, cutting the array off mid-list. `salvage_row_objects` kept the complete rows and
appended a human-readable warning; the reconcile guard (`guards.py::reconcile`) correctly
reported `{"status": "off", "expected": 714.57, "actual": 412.40, "delta": 302.17}`.

Nothing downstream honoured either signal. `decision_preview`
(`backend/services/statements/decisions.py`) still called `preview_ledger_check` unconditionally,
and the UI listed the 12 real charges the truncated parse had never read as "In your ledger, not
on this statement — likely a double-booking," with a one-click "Mark all 17 for deletion" button.
A parse that read less than the whole statement has no basis for the claim "this ledger row is
not on the statement" — it may simply be a row the parse never saw. The same is true, one layer
up, of a statement whose own printed balances don't reconcile: if the statement's own arithmetic
identity doesn't hold, a ledger-only diff on top of it compounds one unproven claim onto another
rather than catching it. This is a money-loss-adjacent path (the fix for "duplicates" here is
deletion of real ledger data), squarely inside the write-choke discipline CLAUDE.md holds
`services/` money math to, even though the ledger-diff panel itself is read-only.

## Decision

We will make an incomplete or unproven parse structurally incapable of producing a ledger-diff
verdict:

1. **Retry before accepting a partial.** `tier3_vision`'s page loop
   (`tiers._read_page_complete`) re-asks a cap-truncated page for its remainder with a
   continuation prompt naming the last complete row, at a raised cap
   (`_ROW_MAX_TOKENS_CONTINUATION = 16384`), up to 3 times, dropping any leading rows the
   continuation re-emits (`_drop_reemitted_rows`, identity-keyed on date/amount/direction/
   description — mirrors the page-boundary rule but scoped to a REtry of the SAME page, where a
   re-read is a real risk, unlike the cross-page case ADR `2026-09-08` covers). `tier2_text`
   already split-retries a truncated chunk; both tiers now stamp
   `meta.extra["parse_complete"]` (False only when rows were genuinely dropped after retry
   budget; tabular is always True — it parses every grid row deterministically). A page still
   truncated after the continuation budget adds itself to `meta.extra["incomplete_pages"]`. A
   page that hit the cap at all (even if a continuation then completed it) sets
   `meta.extra["reasoning_ate_budget"] = True` and a one-line warning naming the fix (pin a
   non-reasoning vision model).
2. **Gate both ledger-diff surfaces on the same predicate.**
   `decisions._parse_withheld_reason(meta, reconcile)` returns `"parse_incomplete"` when
   `parse_complete` is False, else `"reconcile_off"` when the reconcile status is anything but
   `ok`/`unknown`/None (`unknown` — no printed opening/closing — is silence, not a lie, so it
   stays open). `decision_preview` (both the fresh-parse and the cached-parse-reuse branches,
   which converge on one `result` before the ledger-check call) skips `preview_ledger_check`
   entirely when withheld and returns `ledger_check = {"withheld": reason, pages_incomplete,
   delta, currency}` instead. `ledger_diff.uncovered_ledger_rows` applies the identical gate,
   reading the same flags off the record's cached `import_meta`/`reconcile`, before doing any
   covered-set derivation.
3. **The rows that WERE read stay fully importable.** The gate withholds one inference — "the
   rest of your ledger is covered" — never the rows the parse actually produced; commit/import
   proceeds exactly as if `ledger_check` were absent.

## Consequences

### Positive

- The specific incident (real charges offered for bulk deletion because a page silently
  truncated) cannot recur: the withheld state is structural, not a warning the UI could ignore.
- A truncated page usually self-heals for free (the continuation retry), so the gate fires only
  on the genuinely-unresolved remainder — cost stays bounded (3 extra calls per page, worst
  case) rather than either accepting garbage or refusing outright.
- One predicate (`_parse_withheld_reason`) is the single source of truth for both the
  pre-commit and post-commit ledger-diff surfaces — they cannot drift to two different answers.

### Negative

- A statement that reconciles "off" for an UNRELATED reason (e.g. a bank fee the owner disputes,
  a genuinely wrong printed closing balance) now also loses the ledger-diff panel, even though
  every row was read correctly. Accepted: the panel's core claim ("nothing on your ledger is
  missing from this statement") is not a claim we can back once the statement's own arithmetic
  doesn't hold, regardless of why.
- `ok_if_flipped` is treated as failing (withheld) even though it is a proven reconcile, just
  under the flipped sign convention — a deliberately conservative reading rather than trusting a
  guessed-at convention for a deletion-adjacent inference.
- Three extra vision calls in the worst case (a page that never catches up) adds latency/cost to
  an already-expensive tier; bounded, but not free.

### Follow-ups

- Frontend (`ImportModal.tsx`): render no "not on statement" band/bulk-delete button when
  `ledger_check.withheld` is set; show one amber notice with a "Re-read the file" action
  (forceReparse). Tracked as the second commit on this lane.
- `docs/INVARIANTS.md` gets one line recording the rule (this change).
- Not extended to `decisions._manual_link_ids` (the commit-time manual-merge target set) or
  `lucius_pass.py`'s own `preview_ledger_check` call — out of scope for this incident; a
  follow-up should evaluate whether a manual link into an unproven-complete uncovered set needs
  the same gate.

## Alternatives considered

- **Refuse the import outright on a truncated/off statement** — rejected: the rows that WERE
  read are still real, verified data (span_verify, reconcile-per-row); refusing the whole import
  throws away correct work to protect against one derived inference.
- **Keep the ledger-diff panel but add a loud warning banner instead of withholding it** —
  rejected: the incident's own screenshot HAD a warning (the reconcile delta) and it was ignored
  by both the UI and the operator under time pressure; the failure mode is a UI that offers a
  destructive action next to an ignorable caveat, not an absence of caveats.
- **Trust `ok_if_flipped` as complete** — rejected: while its own delta math passes, shipping a
  bulk-delete affordance on an unproven sign-convention guess is the same risk class as an
  outright "off" verdict for this specific, deletion-adjacent surface.
- **Gate only `decision_preview`, leave `uncovered_ledger_rows` alone** — rejected: the
  post-commit panel reads the same cached parse and would otherwise reproduce the incident on a
  reopened statement (RFC 2026-07-29 Phase 4's own "one covered set, both sides of the commit"
  precedent applies identically here).

## Agent provenance

- **Decided by:** implementing agent, senior-engineer lane
- **Lane / branch:** `lane/B16-parse-completeness-gate`, worktree
  `.claude/worktrees/lane+B16-parse-completeness-gate`, based on `fix/statement-import-hardening`
  (PR #111)
- **Alternatives weighed:** refuse the whole import · warn-only · trust `ok_if_flipped` · gate
  only the pre-commit surface
- **Driving artifacts:** owner incident report (2026-09-08, 5-page scanned statement, page 4
  cap-truncated); `backend/services/statements/tiers.py::tier3_vision`/`_row_complete`;
  `backend/services/statements/decisions.py::decision_preview`;
  `backend/services/statements/ledger_diff.py::uncovered_ledger_rows`; `guards.py::reconcile`
- **Why this way (one line):** a parse that never finished reading, or a statement whose own
  balances don't add up, has no basis for telling a missing ledger row apart from one it never
  saw — so the inference is withheld structurally rather than trusted with a caveat.
