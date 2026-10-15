---
title: Merge update_transaction + bulk_update_transactions into one update_transactions tool
status: Accepted
date: 2026-07-19
deciders: reliability-program agent (Claude) + repo owner (harness reliability sweep)
---

# Merge update_transaction + bulk_update_transactions into one update_transactions tool

## Context

The owner watched Lucius issue **7 separate `update_transaction` calls** — 7 round trips, 7
confirm cards, 7 raw confirm-token lines in the chat — for "update memo for these
transactions as 'test'" on 7 selected rows.

`update_transaction` (single-row) sat in `CORE_TOOL_NAMES`; `bulk_update_transactions`
(many rows, one shared change-set) did not. The model only ever saw the singular tool in
its default offer and had no reason to go `find_tools`-hunting for a bulk variant it
couldn't see — even though `bulk_update_transactions` already supported exactly this
operation in one call. This is the identical asymmetry `add_transaction` +
`bulk_add_transactions` had before they were merged into `add_transactions` earlier in this
program; the same fix was never applied to the update side.

A second, narrower gap: even a model that discovered `bulk_update_transactions` could only
apply ONE shared change to every id in the batch — "set a different category on each of
these 5 rows" needed one call per row regardless.

A third, separate gap found while implementing the fix: `bulk_update_transactions` wrote
immediately with no confirm card at all, unlike the gated `update_transaction` — an
inconsistency in write-safety, not just discoverability.

## Decision

**Merge `update_transaction` + `bulk_update_transactions` into one tool, `update_transactions`**,
mirroring the `add_transactions` merge's naming, alias handling, and "one card for N rows"
confirm behavior.

**Shape — `updates: array`, each item `{transaction_id, ...changes}`.** This single shape
covers both original cases: the same change on many rows (repeat the fields in every item)
and a different change per row (vary them) — the thing a shared-changes-only bulk shape
structurally could not express. The single-row case stays trivial: a bare object, or bare
top-level fields with no `updates` wrapper at all (the exact pre-merge `update_transaction`
call shape), are still accepted at the CODE level and route through `_update_one`, the same
"lone item never touches the bulk machinery" pattern `add_transactions`' `_add_one` already
established — but this leniency is NOT advertised in the schema, exactly mirroring how
`add_transactions`' own bare-fields convenience for the `add_transaction` alias was never
declared as a schema-level option either.

**First draft used a list-or-scalar `anyOf` at the `updates` parameter itself** (this
repo's `_txn_schema.py` already uses that idiom for "one or many" filters —
`account_ids`/`category`/`tag`/`payee_id` on `get_transactions`) — `anyOf(one patch
object, an array of them)`. **Reverted after `tests/test_tool_schema_budget.py` failed**:
measured, an `anyOf` branch pair over this rich a per-row shape (17 fields) roughly DOUBLES
the schema's serialized size, because Anthropic's tool-calling JSON schema can't `$ref` a
shared definition between the two branches (`_txn_schema.py`'s own docstring already notes
this constraint) — the object schema and the array's `items` schema each carry a full,
independent copy of every field. That alone tipped `TOOL_SCHEMAS`' total from 298,058 to
302,247 chars, over the suite's 302,000-char budget by 247. Switching to a plain
`updates: {type: array, items: ...}` — matching what `add_transactions`' `transactions`
parameter ACTUALLY is (an array only; its single-row convenience is undocumented code
leniness, not a schema-level `anyOf`) — dropped the tool's own contribution from 9,714 to
5,525 bytes (smaller than the pre-merge `update_transaction` + `bulk_update_transactions`
combined, 7,488) and is the truer mirror of `add_transactions` besides.

**`cleared`/`needs_review` are promoted onto the per-row patch.** They were
`bulk_update_transactions`-only fields; a caller updating a single row previously had no way
to flip them without a second tool. `needs_review` flows through the same generic
`services.ledger.update_transaction` rebuild every other patch field already used.
`cleared` does NOT — it has its own dedicated choke, `services.ledger.
set_transaction_cleared` (a narrower Starting-Balance guard: `cleared=true` is allowed on
an opening row, `cleared=false` is refused) — so the tool splits it out and calls that
choke separately, exactly mirroring how `bulk_update_transactions`'s service function
already handled it internally.

**Old names: decorator `aliases=`, not the retroactive `_LEGACY_TOOL_ALIASES` table.**
`registry.py`'s own convention states the choice plainly: `@tool(..., aliases=(...))` is
"the right choice when you do the rename"; `_LEGACY_TOOL_ALIASES` is for a rename "recorded
RETROACTIVELY, after stored data has already gone stale." This is a same-commit rename —
the direct precedent is `add_transactions`, which used decorator aliases for the exact same
shape of merge. `aliases=("update_transaction", "bulk_update_transactions")` resolves both
old names at dispatch and at every stored-list read, at zero prompt cost (aliases never
appear in `build_tool_schemas()`). No entry was added to `migrate_tool_names.RENAMES` /
`registry._LEGACY_TOOL_ALIASES` for the same reason `add_transactions` didn't get one:
that table's own pinned test (`mig.RENAMES == registry._LEGACY_TOOL_ALIASES`) would need
BOTH tables touched together, and the decorator mechanism already makes the alias
permanent and complete without it.

**The pre-merge `bulk_update_transactions` shape is still recognized, but not advertised.**
A legacy caller sending `{transaction_ids: [...], category: ..., cleared: ..., ...}` (no
`updates` key) is detected and routed straight through the UNCHANGED
`services.ledger.bulk_update_transactions` service function — not decomposed into the new
per-row loop. That function has real behavior the generic per-row path does not reproduce:
additive tags (a UNION with each row's existing tags, vs. the generic patch's REPLACE
semantics), kind-aware skip-with-reason (an adjustment row skips a category change instead
of failing; a transfer/adjustment row skips a payee change), and an auto-move
`to_account_id` sugar that re-points whichever leg applies by kind. Reimplementing all of
that on the new shape risked silently changing behavior for an existing stored caller;
routing unchanged avoids that risk entirely. This path is not in the new tool's schema
`properties` — a new caller only ever sees `updates` — but the code still honors it so a
stored skill/agent calling the old name keeps working byte-for-byte.

**The confirm gate is extended to cover EVERY shape, including the legacy one — a
deliberate hardening, not an accident.** `update_transaction` was already in
`GATED_TOOL_NAMES`; `bulk_update_transactions` never was, so a bulk edit wrote immediately
with no card. `GATED_TOOL_NAMES` now names `update_transactions`, and `should_gate()` is
checked once per call regardless of which shape was used — closing that write-safety gap
was judged worth the behavior change, since an ungated multi-row edit is exactly the kind
of "succeeded and was still wrong" the gate exists to prevent (the same forensics finding
that motivated gating `update_transaction` in the first place).

**Bulk preview is built in `ai/tools/ledger.py`, not `services/ledger_preview.py`.** This
lane's edit scope was `backend/ai/tools/ledger.py` + `backend/ai/tools/_txn_schema.py` +
their tests + alias tables — `services/ledger_preview.py` is out of scope (owned by the
confirm-gate lane). `_preview_bulk_update()` therefore loops the EXISTING public
`ledger_preview.preview_update()` per row (a pure read, no store write) and aggregates a
12-row sample + per-field change tally locally, rather than adding a new function to that
module. The one gap this leaves — `cleared`/`needs_review` never appear in
`preview_update`'s `_DIFF_FIELDS` walk, since neither flowed through
`create_transaction`'s rebuild when that walk was written — is closed by hand-computing
their before/after and appending it to the returned `fields` list
(`_preview_one_update`), rather than teaching `ledger_preview` two new diff fields.

## Consequences

- The owner's exact repro (`update memo for these transactions as 'test'` on 7 rows) is now
  one `update_transactions` call, one confirm card, one confirm-token line — not 7.
- "Set a different category on each of these 5" is now expressible in one call via the
  `updates` array; it was not expressible at all before (neither tool supported it).
- A single-row edit is byte-identical in call shape to before the merge (the bare
  `{transaction_id, ...}` form) and gets the same full resolved `txn_update` card, never a
  generic bulk card with `count=1`.
- `bulk_update_transactions` callers (stored skills/agents, in-flight transcripts) are
  unaffected in write behavior, but now see a confirm card where they previously did not —
  an intentional, disclosed change (see above), not a silent one.
- A batch where any row fails to preview (unknown id, unknown category, a Starting-Balance
  refusal, …) aborts wholesale — nothing is staged — mirroring `add_transactions`' Phase-1
  atomicity contract for creates. The ACTUAL WRITE of a multi-row `updates` batch is NOT
  atomic (each row is an independent `services.ledger.update_transaction` call, like the
  pre-merge bulk tool) — a partial failure reports per-row in `results`, it does not roll
  back rows already committed.
- `tests/golden/tool_input_schemas.json` was hand-edited: the `update_transaction` and
  `bulk_update_transactions` entries were removed and one `update_transactions` entry
  (extracted programmatically from the live registered schema, then reviewed) was added —
  no other entry in that file was touched.

## Agent provenance

Built by a worktree agent in the `harness/reliability-program` lane, scoped to
`backend/ai/tools/ledger.py`, `backend/ai/tools/_txn_schema.py`, their tests, and the alias
tables. Decisions an agent made that a human should be able to see and reverse:
1. Chose the `updates: array` shape over keeping `transaction_ids` + shared-changes as a
   first-class documented alternative (kept it working, just undocumented) — judged that
   offering two competing "how do I edit many rows" shapes to the model would cost more in
   confusion than the terseness it buys for the identical-change-to-many case. Also chose a
   plain array over an `anyOf(object, array)` after the latter tipped the schema-size
   budget test over its cap (see "Shape" above) — single-row triviality is preserved at the
   code level instead of the schema level, matching `add_transactions`.
2. Chose decorator `aliases=` over `_LEGACY_TOOL_ALIASES` + `migrate_tool_names.RENAMES`,
   following `add_transactions`' precedent and `registry.py`'s own stated convention over a
   more literal reading of the task brief that could be satisfied either way.
3. Left `delete_transactions`/`bulk_delete_transactions` unmerged: both are already plural
   (no singular alternative hides them from a bulk caller), so the CORE-visibility
   asymmetry that motivated this merge does not apply there. They ARE two tools doing
   materially the same soft-delete with different confirm-requirement defaults — a
   real but DIFFERENT finding (mirrors the `rollforward_import_batch`/
   `restore_import_batch` "two registry slots for one function" case CLAUDE.md already
   calls out as a separate decision, not a naming fix) — left for its own scoped task.
   `set_transaction_cleared` (in `backend/ai/tools/accounts.py`, a different agent's file)
   has the same never-in-CORE / no-bulk-equivalent shape, but its bulk need is now already
   covered by `update_transactions`' promoted `cleared` field; not edited here (out of file
   scope).
