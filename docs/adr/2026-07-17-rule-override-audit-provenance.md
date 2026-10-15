---
title: Record rule-override provenance on the create audit event, not by moving rules post-save
status: Accepted
date: 2026-07-17
deciders: Claude (interactive session) + repo owner
---

# 2026-07-17. Record rule-override provenance on the create audit event, not by moving rules post-save

## Context

Spend rules run INSIDE `create_transaction` as the last label-only transform before persist
(a stated CLAUDE.md invariant; every import path converges on the one hook). An
`override_existing` rule may therefore silently replace an explicitly supplied category —
a reported bug, 2026-07-17: the Transaction & Trade Logger agent booked a GrabFood order as
"Daily Meals" and reported that in its run summary, while the "Grab/Uber → transport" rule
(`description_regex: "grab|uber"`, `override_existing: true`) stored "Transport". The audit
trail showed a bare "created" event: from the log's perspective the row was simply *born*
with the rule's category. The only trace a rule left anywhere was its own
`last_matched_at`/`match_count` bump. The transaction drawer's Activity timeline — the
feature that exists to explain provenance — could not explain the single most surprising
mutation in the ledger.

Mid-implementation the owner asked whether rule application should instead be **shifted to
after saving/creating**, so the override would surface naturally as a second, diffed audit
event.

## Decision

We will keep rule application exactly where it is (pre-persist, inside
`create_transaction`) and make the create audit event carry the missing fact instead:

- `services/rules.apply_rules` stamps internal hints (`_category_rule_id`,
  `_category_rule_name`) on the in-flight row whenever a rule sets the category; the ledger
  choke pops them (they never persist).
- `create_transaction` and `bulk_create_transactions` stamp a sparse `rule_applied` payload
  onto the create event — `{rule_id, rule_name (at write time), category_from (resolved
  caller ref | None), category_to}` — only when the rule actually changed the outcome
  (an override of an explicit category, or a fill of a blank one).
- `audit.activity()` renders it on the created timeline item as a note ("Rule '<name>' set
  the category", composed with any existing event note) plus a `category` field diff using
  the drawer's existing `changes` contract — zero frontend changes.
- The provenance computation is best-effort and never raises (it runs after the row has
  durably persisted; same contract as `_promote_email_snapshot`/`_bump_matched_rules`).

## Consequences

### Positive

- The drawer now explains rule-decided categories: "Created · Rule 'Grab/Uber → transport'
  set the category · Category: Daily Meals → Transport".
- One write, atomic: what's stored is still what the rules produced, inside the same
  checkpoint'd write. No transient pre-rule state is ever observable.
- Rule name is captured at write time, so the record survives rule rename/delete.
- Additive event field (sparse key) — old events and every existing consumer are untouched.

### Negative

- The audit event schema grows a bespoke field rather than reusing the generic
  `before`/`after` diff; the timeline renderer needs a dedicated branch for it.
- Only the CATEGORY decision is recorded — a rule's tag additions / note appends remain
  invisible in the trail (deliberate scope cut; category is the load-bearing mutation).
- Historical rows (created before this change) have no provenance and cannot be
  reconstructed.

### Follow-ups

- CLAUDE.md "Transaction audit" line + `.claude/rfcs/transaction-provenance.md` data model
  updated in the same change.
- The live "Grab/Uber → transport" rule still mislabels GrabFood orders — a separate fix
  (narrow the regex / drop `override_existing`), tracked outside this ADR.

## Alternatives considered

- **Shift rule application to after the save** (create with the caller's category, then a
  second rule-actor "update" event with a natural before/after diff) — rejected because it
  doubles ledger writes + checkpoint hooks on every import path (worst in bulk), creates a
  transient wrong-category state observable by anything reading between the writes
  (notifications, SSE-driven refetches, dedup/matching, budget folds), turns a today-atomic
  operation into one that can half-fail (rule pass fails → un-ruled row persists silently),
  and breaks the stated "apply_rules is the last transform inside create_transaction"
  invariant with a blast radius across the update path, history backfill, and import
  preview. It would also fabricate an audit narrative: an "update" implies a settled prior
  state, but under the current design the row never existed as "Daily Meals".
- **Record a synthetic second audit event (create + rule-update) while keeping the single
  write** — rejected: same fabricated-narrative problem, plus two events per create bloat
  the trail for the common fill-from-blank case.
- **Do nothing / rely on the rule's own match stats** — rejected: exactly the observability
  gap that caused the live incident.

## Agent provenance

- **Decided by:** Claude (interactive debugging → implementation session)
- **Lane / branch:** `feat/batch-aware-dedup` working tree (change is independent of that
  lane's dedup work)
- **Alternatives weighed:** stamp provenance on the create event vs. move rules post-save
  vs. synthetic second event vs. status quo
- **Driving artifacts:** `.claude/rfcs/transaction-provenance.md`;
  `tests/test_rule_audit_provenance.py`; a reported bug (Grab GrabFood txn
  `txn_0e707c810000`, 2026-07-17); owner question "should the rule be shifted to after
  saving/creating instead?"
- **Why this way (one line):** The gap is observability, not write ordering — record the
  fact on the event the write already emits instead of re-ordering a load-bearing atomic
  choke point to make the fact fall out.
