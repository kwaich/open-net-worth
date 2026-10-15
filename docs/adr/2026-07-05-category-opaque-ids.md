# 2026-07-05 — Categories are opaque ids, not human-readable keys

**Status:** accepted (owner-approved plan, 2026-07-05)
**Where:** `backend/db/category_store.py`, `backend/models/category_defs.py`,
`backend/services/categories.py`, `backend/services/envelopes.py` (`is_commitments_synced`,
`force_delete_commitment_envelope`), `backend/services/planning.py`
(`sync_commitment_envelopes`), `backend/db/commitments_store.py` (`envelope_id`),
`backend/services/equity.py` + `backend/services/options.py` (vest/premium booking),
`backend/services/ledger.py` (write-choke + read-filter resolution), `backend/services/
import_csv.py` / `import_qif.py`, `backend/api/ledger.py`, `backend/ai/tools/categories.py`,
`backend/ai/tools/planning.py`, `backend/scripts/migrate_category_opaque_ids.py`,
`backend/scripts/golden_budget_snapshot.py`

## Decision

A category's identity is now an **opaque, immutable id** (`cat_<12hex>`, minted via
`core.ids.new_id("cat")` — the same convention as `env_xxx`/`txn_xxx`), never a human-readable
string derived from its label:

- `CategoryStore`'s `id_field` flips from `"key"` to `"id"`. Rows are
  `{id, label, kind, color, taxable_default?, hidden?}` — `label` is the ONLY human-readable
  field; nothing derives an id from it. `normalize_key()` (the old lowercase/underscore
  slugifier) is DELETED.
- `models.category_defs.BUILTIN_CATEGORIES` seeds carry STABLE hardcoded ids
  (`cat_food`, `cat_transport`, … `cat_stuff_i_forgot`) so a fresh install is reproducible and
  tests can reference them directly. The structural `split` sentinel keeps the literal id
  `"split"` — the ONE row that is not opaque, since it is a marker (not a real category), per
  the pre-existing `STRUCTURAL_KEYS` invariant.
- `create_category(label, kind, color, taxable_default, group)` mints an id server-side — the
  caller never supplies or chooses one. `rename_category(key, new_label=None,
  merge_into=None)` collapses to two modes: **relabel** (the id never changes, so nothing else
  is ever re-keyed) or **merge** (fold `key`'s transactions/split-lines/legacy budget cap/group
  membership/rules/envelope mapping into an existing `merge_into` id, then delete `key`'s row).
  The old `rename_category(old_key, new_key, ...)` re-key API is gone — there is no way to
  change a category's own id anymore.
- **The system-category concept is retired** (decision #3): `equity-compensation` /
  `options-premium` are no longer auto-seeded. Vest income (`services/equity.py`) and option
  premium income (`services/options.py`) now stamp `taxable=True` directly on the transaction
  and leave `category=None` — `services.categories.txn_is_taxable()` already resolves the same
  `True` either way (verified: `services.tax._employment_income_section` counts by `kind` +
  `txn_is_taxable`, never by category name), so this is a mechanism change with an identical
  tax-engine outcome.
- **Commitments get a real foreign key, not a bot-managed category** (decision #4):
  `commitments.json` rows gain `envelope_id`; `planning.sync_commitment_envelopes` (renamed
  from `sync_commitment_categories`) mints/retires a **zero-category** envelope per active
  commitment and stamps the FK — no `commitment_{id}` category is minted at all anymore.
  `envelopes.is_commitments_synced` now checks the FK (`any commitment.envelope_id == this
  envelope's id`) instead of a `category_keys` prefix scan.
- A **name→id resolver**, `services.categories.resolve_category_ref(ref, rows=None)`, is the
  one real behavioral addition: it resolves a category id OR a label (case-insensitive exact
  match) to the stored id. It is wired at the two places most likely to receive a raw label
  instead of a pre-looked-up id — the ledger write choke (`create_transaction` /
  `bulk_create_transactions` / split validation) and foreign CSV/QIF import — plus, as a
  low-risk read-side extension, `services.ledger.get_transactions`'s category filter (so "show
  me my food spend"-style AI queries keep working against a label). Everywhere else (AI tools,
  rules, REST), the existing convention holds: the caller calls `list_categories` first and
  passes back the exact id it was given — no resolution needed there, since that id was never
  "raw" to begin with.

A coupled one-shot migration, `backend/scripts/migrate_category_opaque_ids.py`, re-mints every
existing category (built-ins to their stable seed id, custom categories to a fresh random id,
`split` unchanged) and re-points every reference: `transactions.category` + `splits[].category`,
`rules.set.category`, `payees.default_category`, `envelopes.category_keys[]` +
`former_category_keys[]`, `category_groups.categories[]`, `profile.monthly_budgets` keys,
`corrections.{from,to,target}_category`, `scheduled_tasks[].transaction.category`, and the
`categories.json` rows themselves. A `commitment_{id}` category is RETIRED, not re-keyed: its
FK (`commitment.envelope_id`) is backfilled from whichever envelope already linked it, and any
stray ledger reference is cleared to `None` (unsorted) rather than dangling. Idempotent (a
second run is a no-op), never auto-run at boot.

## Why

The natural-key design meant a category's identity, its display label, and its ledger
reference were all the same string — renaming a category was a full re-key across every store
(the exact shape of bug class the envelope-id-native cutover just eliminated for envelopes one
layer up). Collapsing to an opaque id removes an entire class of "rename broke my rules/
splits/budgets" bugs by construction (the id never changes, so there's nothing to re-key on a
relabel), and it removes the kind-name/category-key collision risk structurally (a randomly
minted id can never literally equal `"income"`/`"transfer"`/etc. — the `_validate_reserved_
category` guard at the write choke stays as insurance against a raw untrusted string, but the
store-level `_KIND_NAME_KEYS` check in `create_category` is now moot and was removed).

Retiring the two system categories (`equity-compensation`/`options-premium`) removes a
built-in-that-can't-really-be-deleted awkwardness: a taxability rule that lives on the
transaction directly is simpler and doesn't require a magic always-present category a user
might accidentally rename/merge/hide and break tax reporting. Giving commitments a real FK
instead of a synced pseudo-category removes an entire (category, envelope) pair that only
ever existed to carry one number (the monthly target) — a zero-category envelope already does
that on its own.

## Consequences

- **Frontend follow-up required before this is user-facing.** `GET /api/categories` (and
  every payload that embeds a raw category row) now returns `{"id": ...}` instead of
  `{"key": ...}`. The frontend currently reads `.key` off these rows in several places
  (`frontend/src/hooks/useCategories.ts`'s `CategoryMeta.key`, `frontend/src/hooks/useBudget.ts`,
  `frontend/src/components/tabs/plan/categoriesManager.tsx`,
  `frontend/src/components/tabs/plan/budgetEnvelopeOps.tsx`). Deliberately **out of scope for
  this change** (this migration's verification surface is backend-only: pytest, ruff,
  import-linter, import smoke, and a clone-migration sanity check — no frontend build/test was
  run or is claimed here) — but the Manage Categories UI and any other consumer of a raw
  category row's `.key` WILL render broken (`undefined` ids) until a follow-up pass renames
  every such read site to `.id` and clears the UI Quality Gate. `frontend/src/hooks/
  useCategories.ts`'s `BUILTIN_CATEGORY_KEYS` set (a hardcoded list of old literal keys) also
  becomes permanently non-matching post-migration; it drives an unused `.builtin` flag (dead
  cosmetic code, confirmed via grep — no runtime consumer reads it for categories), so this is
  a cleanup opportunity rather than a functional break, but should be removed in the same
  follow-up.
- A category's label is the only thing rename ever touches now; `rename_category` no longer
  takes a destination key at all — merging two categories is an explicit `merge_into` call
  with an existing id, not an implicit "new_key already exists" collision.
- `services.budgets.EXPENSE_CATEGORIES`, `api.ledger.category_to_kind()` (was a static dict,
  now a function reading the live store), and every other set/map keyed by category now
  contains opaque ids, not slugs.
- Money math is unaffected: `golden_budget_snapshot.py`'s `months` section (assigned/activity/
  available/target/RTA/totals, all keyed by `envelope_id`, never a category id) is
  BYTE-IDENTICAL before and after the migration on a real, non-trivial live book (4 months,
  hundreds of transactions, 5 active commitments). Its new `category_integrity` capture (a
  per-category txn census keyed by LABEL — the one field stable across the id rename) shows
  zero `__unresolved__` entries after migration, and a handful of pre-existing dangling
  references (`category="investment"` on one stray transaction, two orphaned envelope
  `category_keys` entries, several `env_xxx`/`stuff_i_forgot` entries in `category_groups`
  display lists) are provably PRE-EXISTING — identical sets before and after — not introduced
  by this migration.
- The migration is idempotent and was run against an isolated clone of the real book (never
  the live `~/.open-net-worth` tree): 28 categories re-keyed, 5 `commitment_*` categories
  retired (all 5 successfully linked to their existing envelope via the new FK, zero
  orphaned), 702 transactions / 3 rules / 240 payees / 28 envelopes / 8 category_groups / 111
  corrections re-pointed, a second run reported "nothing to do".

## Agent provenance

Implemented end-to-end (store/model/service layer, REST, AI tools, CSV/QIF import, the
migration script, and the extended golden-snapshot proof) as a single coupled change per the
owner's locked plan. The frontend wire-contract break (`.key` → `.id`) was identified during
implementation and deliberately deferred to a dedicated, UI-Quality-Gated follow-up rather than
silently expanding this change's scope — flagged here and in the handoff report so it isn't
lost.
