---
title: Resolve opaque ids at the read, and stop serving dead slug-keyed profile fields
status: Accepted
date: 2026-07-20
deciders: repo owner (confabulation incident report) + lane/resolve-ids-and-profile (Claude)
---

# Resolve opaque ids at the read, and stop serving dead slug-keyed profile fields

**Relates to:** `2026-07-05-category-opaque-ids` (the migration that copied and never
deleted), the 2026-07-09 historical-rate-lock scar (`_project_txn` is `return dict(t)`),
`2026-07-19-bound-tool-results-filter-first` (the "never add a caller-chosen projection
parameter" rule this change deliberately does not violate).

## Context — the evidence

Two defects with one root cause: **the model is handed opaque ids with no meaning, and stale
slug-keyed data that contradicts them.**

### The confabulation incident

Owner transcript `~/.open-net-worth/data/sessions/5c535367-2336-4239-9992-48aedaa33cd0.json`,
messages 5-10. `get_transactions` returned rows like:

```json
{"id": "txn_4bec0a390000", "category": "cat_13095ba70000",
 "payee_id": "pay_16d9461e0000", "from_account_id": "acc_c98e05230000",
 "description": "Grab* A-EXAMPLE0000000", "base_amount": 14.54}
```

Every entity reference is an opaque id with no label anywhere in the payload. The model
answered from the ids alone and produced **both** failure modes at once:

- it **INVENTED "Food & Dining"** for two rows — a category that does not exist in this book.
  The real stored category behind `cat_13095ba70000` is **"Daily Meals"**. The fabrication was
  presented as the stored value.
- it reported the **other three as having NO category**, when all three carried ids.

Only after the owner challenged it did it call `list_categories` and correct itself. **Neither
error was flagged as uncertain.** This is silent, confident wrongness on money data.

**Provenance of these claims.** The session JSON has since been rotated away (only
`5c535367-….extraction_cursor.json` survives), so the narrative above is the owner's report,
not a transcript this lane re-read. What WAS verified first-hand against
`~/.open-net-worth/data/categories.json` (36 categories): `cat_13095ba70000` really does
resolve to **"Daily Meals"**, and **no category named "Food & Dining" exists**. One detail the
report did not mention makes it worse — the book *does* contain a category called **"Dining
out"**. So the fabricated name was close enough to a real category to survive a skim, on a
book where the correct answer was a *different* real category. That is the failure this ADR is
about: not an obviously-wrong answer, but a plausible one.

The model *had* the ids, *had* `list_categories` available, and answered anyway. That is the
load-bearing fact: the id→label join is one the model demonstrably will not reliably perform.

### The contradicting data

`get_profile` is a CORE tool (in the ~13-tool cold offer), so **every session** paid for five
fields a completed migration was supposed to have removed: `custom_categories`,
`deleted_builtins`, `hidden_categories`, `category_color_overrides`, `category_notes` —
measured at **1,696 of 3,599 response bytes (47%)** on the owner's live profile.

Worse than the bytes: they are keyed by **retired slugs** (`"startup"`, `"family"`,
`"sister_support"`, `"stuff_i_forgot"`) while category ids have been opaque `cat_<12hex>` since
the opaque-ids migration. The model received **two contradictory shapes for one concept, both
presented as current** — while simultaneously being unable to resolve either.

`backend/main.py:378` asserted these fields "were migrated off once and are gone." They were
not: the migration **COPIED** into `CategoryStore` and never deleted the originals. A comment
asserting a state that does not hold is very likely why nobody re-checked.

## Decision

### 1. Per-row labels, not a legend

Money-bearing reads attach the label the server already has, **beside** each opaque id:
`category_label`, `payee_name`, `from_account_name`/`to_account_name` on transactions (and on
each **split line** — a split row stores `category="split"` up top, so the per-line categories
are otherwise the only category information in the payload and all of it is unresolved);
`instrument_label`/`instrument_ticker`/`account_name` on lot and income-event rows.

A legend (`{id: label}` beside the rows) was the obvious alternative and was **rejected**:

- **Correctness (decisive).** A legend requires precisely the join the model failed to
  perform, offered a second time in the same response. Per-row makes skipping it structurally
  impossible.
- **Cost (not decisive, and it favours per-row anyway on real traffic).** A legend pays the
  expensive opaque id **twice** (once as its own key, once in the row) where per-row pays it
  once, so it only wins after each id repeats ~3×. 83.5% of real `get_transactions` calls in
  the session corpus are ≤25 rows; the incident itself was `limit: 5`, where per-row is
  outright cheaper.

Deliberately **not** a size-conditional hybrid: two shapes for one concept is exactly defect
2's pathology.

`get_portfolio` already ships this shape (`account_name`/`label`/`ticker` beside its ids), so
this makes the ledger **consistent with the tool that already got it right** rather than
introducing a second convention.

### 2. Drop four dead profile fields — and keep the fifth

`services.profile.read_profile()` strips `custom_categories`, `deleted_builtins`,
`hidden_categories`, `category_color_overrides`. Each was proven dead by grep first:

| field | readers found | verdict |
|---|---|---|
| `custom_categories` | only `migrate_investment_kind.py` (a spent one-shot, reads defensively with `or []`) | dead |
| `deleted_builtins` | same | dead |
| `hidden_categories` | `services.categories.hidden_categories()` reads the **STORE**; the frontend's two hits are stale **comments** (live path is `POST /api/categories/{key}/hide`) | dead |
| `category_color_overrides` | none anywhere in `backend/` or `frontend/` | dead |
| **`category_notes`** | **`services.planning.read_category_notes`/`write_category_note`, `services.envelopes._read_category_notes`, `GET`/`PUT /api/planning/notes`, the `get_envelope_note` tool** | **LIVE — NOT removed** |

**The brief called all five dead. It was wrong about `category_notes`**, and this is the one
finding worth carrying forward: its stored *value* on the owner's book IS stale (slug-keyed,
`{"sister_support": "Help her with apartment's purchase payment"}`) — but a stale value is a
**data** problem to re-key, not a dead field to drop. Deleting it would have destroyed a note
the user wrote. Staleness of the value and deadness of the field are different questions, and
the grep is what separates them.

### 3. An explicit migration that deletes

`backend/scripts/migrate_profile_drop_dead_category_fields.py` — dry-run default, `--apply`,
timestamped backup, atomic write, idempotent, `--file COPY` for testing, **not** a boot
migration. The earlier migration copied and never deleted; this one deletes. The read path
strips too, so an un-migrated install behaves correctly either way.

`migrate.DEAD_FIELDS` and `services.profile._DEAD_CATEGORY_FIELDS` are pinned equal by a test
so the two cannot drift into "the read serves what the migration deleted" or vice versa.

## The hazard this does NOT trip

`ai/tools/ledger.py::_project_txn` is deliberately `return dict(t)` after the 2026-07-09
hidden-field incident, and the rule is: **never add a caller-chosen `fields`/projection
parameter to a money-bearing read** — it lets the model select `amount` over `base_amount` and
silently defeat the historical-rate lock.

This change is **server-chosen and additive**: it selects nothing away, removes nothing,
renames nothing, and adds no `fields`/`include`/projection parameter. Row selection is safe;
field projection is not — and this is neither, it is field *addition*. Pinned by
`test_labelling_is_purely_ADDITIVE_and_removes_no_money_field`, which asserts every stored key
survives with an unchanged value and that the only new key is the label.

Two further correctness guards worth recording, both learned from writing it:

- **Labelling must COPY before it writes.** `dict(t)` is shallow, so labelling a split line in
  place would write the label back into the store's own cached row. Pinned by
  `test_split_lines_are_labelled_without_mutating_the_stored_row`.
- **An unresolvable id attaches NOTHING, never `""`.** Absent reads as "no label available";
  `""` reads as "this thing is named nothing." The second is a *claim*, and a wrong claim
  about a name is the entire bug being fixed here.

## Consequences

Measured against the owner's live data (1,456 txns / 36 categories / 231 payees / 28
accounts), real `cl100k_base` tokenizer, genuine A/B by running the pre-fix code:

| surface | before | after | Δ |
|---|---|---|---|
| `get_transactions` limit=5 | 1,352 tok | 1,467 tok | **+115 (+8.5%)** |
| `get_transactions` limit=25 | 6,370 tok | 6,869 tok | **+499 (+7.8%)** |
| `get_transactions` limit=50 | 12,182 tok | 13,247 tok | **+1,065 (+8.7%)** |
| `get_transactions` limit=100 | 23,697 tok | 25,878 tok | **+2,181 (+9.2%)** |
| `get_profile` | 1,076 tok | 683 tok | **−393 (−36.5%)** |

The design predicted +8.9% for per-row labels across the corpus; measured +7.8% to +9.2%.

**The two defects roughly cancel on a typical turn.** A cold turn that reads the profile and
25 transactions goes 7,446 → 7,552 tokens (+1.4%) — the dead-field removal nearly pays for the
labels outright, and what remains buys the removal of a silent-wrong-money-answer mode.

The incident row now reads:

```json
{"id": "txn_4bec0a390000", "category": "cat_13095ba70000", "category_label": "Daily Meals",
 "payee_id": "pay_16d9461e0000", "payee_name": "Grab",
 "from_account_id": "acc_c98e05230000", "from_account_name": "Meridian Bank Rewards Card", ...}
```

`cat_13095ba70000` — the exact id the model saw when it answered "Food & Dining" — now
arrives with "Daily Meals" attached.

### Class scan: 3 defective of 6, not 5

The design's scan was verified against real payloads rather than trusted. `get_portfolio`
(already ships `account_name`/`label`/`ticker`), `get_budget_month` (every row has `label`,
every group a `name`), `get_signals` (zero opaque entity ids) and `get_income_forecast`
(already ships `symbol` + `name`) are **clean and were not touched** — a surface without the
defect gets no diff.

`get_income`'s `by_instrument` aggregate stays id-keyed deliberately: every one of its keys
also appears in the same payload's `events` **with** its label, so it is resolvable without
minting a second labelled shape for one concept.

## Agent provenance

- A design pass ran first and was **used but verified**; its cost model, its `_apply_q_filter`
  reuse claim and its 3-of-6 class scan all held up against live measurement. Its one
  correction: the "before" token figures must be taken by running the pre-fix code, not by
  re-serialising the post-fix payload minus the label keys — the dispatch layer emits compact
  JSON separators, so a naive re-dump inflates the baseline and reports an additive change as
  a 10% *saving*.
- The brief's claim that all five profile fields were dead did not survive its own required
  proof step ("before deleting each field, PROVE it is dead"). `category_notes` has four live
  call sites. Reported rather than deleted — that instruction is what caught it.
- The pre-existing `make lint` failure (`fetch_emails` description names dead alias
  `add_transaction`) was confirmed present on the base branch with this lane's changes stashed
  and left alone — a different lane's surface.
