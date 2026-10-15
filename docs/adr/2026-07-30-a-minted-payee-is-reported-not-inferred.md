# 2026-07-30 — A minted payee is REPORTED by the write, not inferred by the caller

**Status:** accepted (owner bug report — "why the hell does my transaction-trade-logger keep
forgetting to clean up payee? and categorize?", 2026-07-30)
**Where:** `backend/services/ledger.py` (`resolve_transaction_row`, `create_transaction`,
`bulk_create_transactions`), `backend/ai/tools/ledger.py` (`_add_one`, the bulk result assembly,
`update_transactions`, `_payee_minted_note`)
**Tests:** `tests/test_payee_mint_observable.py` (11)
**Related:** ADR `2026-07-20-resolve-ids-and-drop-dead-profile-fields` (labels on reads — this
extends it to writes); the "silent mint books Unsorted — seen live" comment in
`resolve_transaction_row` that this makes actionable

## Decision

When a ledger write INVENTS a payee from the raw bank description, the write result says so.

1. `resolve_transaction_row`'s `meta["payee_is_new"]` is now true when the call **actually
   minted**, not only when a preview **would have** minted.
2. `create_transaction` and `bulk_create_transactions` report it through a keyword `meta_out`
   dict (`payee_minted_txn_ids`), leaving their signatures and return shapes untouched.
3. `add_transactions` returns `payee_minted: true` on each created row that minted, a batch-level
   `payee_minted` count, and a `payee_minted_note` stating what to do. `add_transactions` and
   `update_transactions` now also return the row through `labels.label_txn`, so `payee_name`
   arrives beside `payee_id`.

Absent when nothing minted — a flag that is always present is not a signal.

## Why

The unattended Transaction & Trade Logger has a long, emphatic POST-CREATE PAYEE CHECK in its
prompt, and it *was* running it: session `scheduled-78a61cbe` created 8 rows and then issued 4
payee-cleanup updates. But it could only fix the rows whose clean payee already existed and was
findable by substring. The rest kept their invented payee:

```
CREATE txn_7026a9400000  payee='Meridian Bank Transfer to NGUYEN VAN A - photo shoot deposit'  cat=None
CREATE txn_a56acdb90000  payee='PayLah! Scan & Pay - DELUXE (SG) PTE L Ref: PLPE0000000000000000'  cat=None
```

The prompt was not the problem. **The condition was unobservable.** The stored row carries
exactly one payee field (`payee_id`), `_add_one` returned that row verbatim, and the resolver's
own `payee_is_new` flag — the one fact that would have made cleanup deterministic — was computed
only on the branch that does not write:

```python
if mint_payee:
    pid = payee_registry.resolve_payee_id(fallback_ref)   # mints, sets no flag
elif (fallback_ref or "").strip():
    payee_is_new = True                                   # flags, writes nothing
```

So the agent was asked to detect, by diligence and extra round-trips, something the server
already knew and discarded. Under a weak model (`openrouter:z-ai/glm-5.2`) on a ~6,000-word
prompt whose checklist sits at the bottom, that step is exactly what gets dropped — and the email
cursor has already advanced, so the row is never revisited.

This is the same failure as the 2026-07-19 incident behind `services/labels.py` (the model got
`category="cat_13095ba70000"`, answered from ids alone, and invented a category name). That sweep
covered money-bearing **reads**; the write results were left returning bare ids, which is where an
unattended writer actually needs them.

**Why it never converged.** Every minted name embeds that row's own reference number, so it can
never match another transaction — 19 junk payees had accumulated by 2026-07-29, one per unmatched
row, each making `get_payees` noisier. And a fresh mint has no default category, so the
`payee-default` tier of `explicit > rule > payee-default > None` is empty by construction: the
junk payee and the "Unsorted" category are **one event**, not two bugs.

## Alternatives rejected

**Sterner prompt wording.** Already tried, at length, by the prompt's own author. You cannot
instruct a model into observing state the tool does not return.

**Stop minting from a raw string** (strip refs, or leave `payee_id` null). This is the actual root
cause and remains open by owner decision — deferred to a separate discussion, since "a blank payee
beats a junk one" changes established write behavior. Reporting the mint is what makes either
outcome recoverable in the meantime, and is orthogonal to that choice.

**Widen the return of the two choke points** instead of `meta_out`. `create_transaction` and
`bulk_create_transactions` have many callers, none of which should have to change to ignore a
fact they don't consume. An out-param keeps the change additive.

**Infer the mint at the tool layer** (snapshot payee ids before/after the write, or compare
`created_at`). Duplicates resolver logic at a distance and re-derives a fact the resolver already
computes — the same "two sources for one answer" shape this repo keeps removing.

## The note names the flow, not the tools

`_payee_minted_note` deliberately contains no tool names and no call signatures, and a test
enforces that. A tool rename only has to keep an alias resolvable, and `tool_inventory.py --lint`
does not read prose — so a signature written into guidance rots invisibly and then actively
misleads. Caught for real while writing this: the plausible `update_transactions(payee_id=…)` is
not the tool's declared shape at all (it takes `updates`). The note states the goal — find the
real merchant, link or create the payee, give it a default category, teach it to match this
wording, retire the junk entity — and lets the model resolve the tools, which it already does via
`find_tools`.

## Agent provenance

Diagnosed from live data before any code was read for editing: `txn_audit.json` (which rows the
agent created vs. updated, per session), `tool_call_log.json` (296 agent calls: 57 `get_payees`,
12 `update_transactions`, **1** `add_payee_rules`, **1** `create_payees`, **0**
`get_category_suggestions`), and `payees.json` (19 ref-bearing junk payees). Two prompt claims
were falsified by that log and corrected in the same change: the agent *can* create payees
unattended (`create_payees` → `ok=True`, 2026-07-28; the `unattended_capabilities` ladder has been
vestigial since the 2026-07-18 permission teardown), and a transaction has **no** top-level `note`
field — `description` is the raw bank string, `memo` is the human-facing detail.
