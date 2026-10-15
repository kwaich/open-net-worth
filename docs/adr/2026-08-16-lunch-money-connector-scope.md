---
title: Lunch Money connector — transactions only, no category mapping, a rolling sync window
status: Accepted
date: 2026-08-16
deciders: repo owner (task brief) + implementing Claude agent (lunch-money-connector lane)
---

# Lunch Money connector — scope and sync design

## Context

Lunch Money (lunchmoney.app) is a third-party budgeting/transaction-aggregation SaaS: a
user who already tracks spending there can bring that data into Open Net Worth's own
ledger. The owner's brief named the shape ("a data source, transactions probably") and
asked for judgment on three specific questions: whether Lunch Money's own manually-tracked
`/assets` are in scope, whether/how to map its categories onto ours, and what sync cadence
to use. It also asked for the closest existing precedent (the SimpleFIN connector) to be
read and followed structurally rather than inventing a new pattern.

The real Lunch Money v1 API was verified live against `lunchmoney.dev` on 2026-08-16 (see
"API shape" below) — it differs from the task brief's `dev.lunchmoney.app` base URL, which
no longer serves the current API.

## Decisions

### 1. Transactions only — `/assets`, `/plaid_accounts`, `/crypto` are out of scope

Lunch Money's `/assets` endpoint returns the user's manually-tracked net-worth items
inside Lunch Money — each one is a raw **stored `balance`** with no transaction history
behind it (confirmed against the live API reference: `balance` is a plain string field,
`balance_as_of` a timestamp, no ledger of how it got there). This app's own invariant is
the opposite: **balances are computed from the ledger, never stored** (CLAUDE.md). There
is no two-leg transaction to construct from a balance snapshot — syncing `/assets` would
mean inventing a new "trust this stored number" data path that fights the app's central
architecture, not a data-mapping problem.

`/plaid_accounts` and `/crypto` are Lunch Money's own read-only views of data sources it
has *already folded into* `/transactions` — a linked bank or crypto exchange's activity
shows up as ordinary transactions with `plaid_account_id`/`asset_id` set. Pulling those
endpoints separately would re-import the same money movement a second time under a
different shape.

**Decision: sync `/transactions` only.** Matches the task's own instinct and framing
("data source... transactions probably") and every sibling connector's shape (SimpleFIN/
Plaid/Enable Banking/Yodlee/IBKR all import transactions into the ledger; only Alpaca/IBKR
touch the portfolio, and only for real holdings, not a third-party app's own net-worth
tracker).

### 2. No category mapping — every sibling adapter already answered this

Lunch Money's `/transactions` response carries `category_id`/`category_name` directly.
Every existing aggregation adapter in this codebase (SimpleFIN, Plaid, Yodlee) contains
the identical comment: `# money in → income (categorization left to rules / the user)` —
none of them attempt to set `category` from anything the provider sends. IBKR's cash-leg
mapper is the one adapter that DOES set a category, but only for its own small, fixed set
of well-known transaction *types* (dividends/interest/fees/taxes) mapped onto this app's
OWN category keys — its own comment is explicit that "IBKR's free-form type names are
never category keys."

This app's `category` field is an **opaque id** (`cat_<12hex>`) that **raises
`ValidationError` on an unknown value** — it is never silently coerced from an arbitrary
label (CLAUDE.md's ledger invariants). Lunch Money's `category_name` is the user's own
free-form taxonomy *inside* Lunch Money, with no relationship to this app's category ids.
Mapping it in would mean either inventing a fuzzy-match layer (wrong precedent — nothing
else in this codebase does provider-category → app-category translation) or refusing sync
whenever a category name doesn't already exist here (a surprising, brittle failure mode
for a routine sync).

**Decision: imported transactions carry `category=None`** (renders "Unsorted"), exactly
like every sibling connector. The categorization tier this app actually uses for connector
imports is the same one every other import goes through: an event-triggered Automation
running after the row commits (CLAUDE.md's "Category precedence at the create choke").
Lunch Money's own category on a transaction is simply not read.

### 3. Group/split parent rows are skipped

Not asked directly by the brief, but discovered while verifying the real API shape and
worth recording because it is a genuine correctness risk unique to Lunch Money (no sibling
connector has this concept). A Lunch Money "transaction group" bundles several real
transactions under one synthetic parent row; the live API reference is explicit that the
parent's own `amount`/`currency` "represent the totalled amount of transactions bearing
this transaction's id as their `group_id`" — and those real children are **separate rows
in the same `/transactions` response**, each carrying `group_id` back to the parent. A
"split" transaction is the mirror case: `has_children=true` on the original row, with the
real per-split amounts living in child rows carrying `parent_id`.

Importing both the parent and its children would double-count the same money movement —
a silent net-worth bug, not a cosmetic one. **Decision: any row with `is_group=true` or
`has_children=true` is skipped**; only leaf transactions (real, unsplit, ungrouped rows —
including a group's or split's individual children) are booked. Documented in the
adapter's module docstring and covered by a regression test
(`tests/test_lunch_money_service.py::test_group_and_split_parents_are_skipped_children_still_import`).

An unassigned transaction (neither `plaid_account_id` nor `asset_id` set — Lunch Money's
own UI treats this as needing manual assignment) is skipped for the same "don't guess"
reason, rather than dumped into a synthetic bucket account.

**2026-08-16 addendum (security review):** this section's "unverified against a real
account" caveat was flagged as a real risk — if the assumption is wrong for some account
configuration, the skip silently deletes the parent's money rather than avoiding a
double-count, and the original implementation gave an operator no way to notice. Fixed by
adding `LunchMoneyAdapter.group_parent_skipped`, a counter kept separate from the generic
`skipped` bucket, surfaced in `stats`/the sync result dict (so it reaches the Connectors UI
and `get_lunch_money_status`/`sync_lunch_money_now`) and logged
(`services/aggregation/lunch_money.py::LunchMoneyAdapter.map`). This does not verify the
assumption — that still requires a real account — but makes a mismatch between "parents
skipped" and "children actually imported" observable instead of silent.

### 4. Sign convention: request `debit_as_negative=true`

Lunch Money's own default (`debit_as_negative=false`) returns an expense as a **positive**
number and income as negative — the opposite of every other adapter in this codebase
(SimpleFIN/Plaid: positive = money in). Rather than inverting the sign inline (an easy
off-by-one-sign bug to introduce silently), the adapter passes `debit_as_negative=true` on
every request, which makes Lunch Money's own API do the conversion — the mapper then
reuses the exact `amount > 0 → income` / `amount < 0 → expense` branch every sibling
adapter already has, byte-identical.

### 5. Sync cadence: a bounded 120-day rolling window, not a cursor, on the SimpleFIN default poll interval

Unlike a bank feed (which returns its own bounded recent window whether you ask for one or
not) or Plaid (a real incremental cursor via `/transactions/sync`), Lunch Money's
`GET /transactions` returns **every transaction ever** when no date range is given. An
unbounded default would make every scheduled poll re-fetch a user's entire multi-year
history. Lunch Money has no published cursor-based sync endpoint in v1, so the adapter
uses a **rolling 120-day lookback window**, re-requested on every sync (paginated via
`offset`/`limit`, Lunch Money's own default page size of 1000), rather than building a
bespoke incremental-cursor scheme against an API that doesn't offer one. External-ref
dedup (the same `bulk_create_transactions(dedup_by_external_ref=True)` floor every
connector relies on) makes the ~4-month re-scan a cheap no-op after the first sync — the
cost is a slightly larger read per poll, not a correctness risk, and a 120-day window is
generous enough to catch a transaction that posts or gets corrected late.

Poll interval: the task asked to match SimpleFIN's convention if no rate-limit
documentation could be found live — none was found (Lunch Money's own developer docs page
does not publish a numeric rate limit as of 2026-08-16). **Decision: 21600s (6h) default,
3600s (1h) floor**, byte-identical to SimpleFIN/Plaid's own numbers.

## API shape actually built against

Verified live against `lunchmoney.dev` on 2026-08-16 (WebFetch/WebSearch — no account, no
live sync round-trip):

- **Base URL: `https://api.lunchmoney.dev/v1`** — differs from the task brief's
  `https://dev.lunchmoney.app/v1/`, which does not serve the current API.
- Auth: `Authorization: Bearer <token>` — matches the brief.
- `GET /transactions`: query params `start_date`/`end_date` (`YYYY-MM-DD`), `offset`/
  `limit` (default page size 1000), `debit_as_negative` (bool, default `false`), plus
  filters not used here (`category_id`, `plaid_account_id`, `asset_id`, `pending`, …).
  Response: `{"transactions": [...], "has_more": bool}`. Transaction fields used:
  `id`, `date`, `amount` (string), `currency`, `payee`, `display_name`,
  `plaid_account_id`/`plaid_account_display_name`, `asset_id`/`asset_display_name`,
  `institution_name`/`asset_institution_name`, `is_group`, `has_children`, `group_id`,
  `parent_id`.
- `GET /categories`: confirmed shape (id/name/is_income/group_id/…) — not called; kept for
  reference since a future feature (category mapping, if ever revisited) would need it.
- `GET /assets`: confirmed shape (`balance`/`balance_as_of`/`type_name`/…) — not called
  (out of scope, decision 1 above).

## Consequences

- A user's existing Lunch Money account becomes a one-token sync into this app's own
  ledger, transactions only, deduplicated on re-sync, matching every sibling connector's
  operational shape (status/sync REST routes, promoted AI tools, scheduler poll job,
  Connectors catalog entry).
- Categorization of Lunch Money imports is left entirely to this app's own rules/
  Automations — a user who wants Lunch Money's categories mirrored here needs a
  categorization Automation, not a connector feature. Revisiting this would need a new
  design (a fuzzy/manual category-mapping UI), not a quick follow-up.
- A late edit/correction made in Lunch Money to an already-imported transaction is not
  reflected here — matches every sibling adapter's create-only behavior (none of them
  update a previously-imported row).
- Lunch Money's own manually-tracked assets never appear in this app. If a future request
  wants them, it is a different feature (most likely a manual-account sync, not a
  transaction-ledger sync) and should be scoped separately.

## Agent provenance

Implemented on `feature/lunch-money-connector` (worktree
`.claude/worktrees/lunch-money`), off `main`. The task brief asked for judgment on scope
(assets in/out), category-mapping strategy, and sync cadence, explicitly deferring to the
implementer where SimpleFIN's own precedent didn't directly answer the question. All three
decisions above, plus the group/split-parent skip (found only by reading the live API
reference in detail, not requested explicitly), were made during implementation and are
recorded here because none is visible from the diff alone. Verified against mocked HTTP
responses matching the live-verified API shape — no real Lunch Money account or access
token exists in this environment, so nothing here is proven against a real round-trip.

## 2026-08-17 addendum: migrated from API v1 to v2

Lunch Money's v1 API is legacy; new integrations are directed to v2
(`https://api.lunchmoney.dev/v2`), which its own migration guide states is **NOT**
backwards compatible with v1. This addendum records what changed and why — the decisions
above (transactions-only scope, no category mapping, group-parent skip, the 120-day
rolling window) all still hold; nothing here reopens them.

**Verified live this session** (not trusted from the task brief alone): the real
`https://api.lunchmoney.dev/v2/transactions` endpoint with a fake Bearer token (confirms
`401` with a proper JSON error body, and strict query-parameter validation — an unknown
param, or the removed `debit_as_negative`, returns `400`); Lunch Money's own hosted
`https://mock.lunchmoney.dev/v2` mock server for `GET /transactions`, `GET
/plaid_accounts`, `GET /manual_accounts`, and `GET /categories` (confirms every field name
below against real response bodies, not just prose); and the migration guide at
`https://alpha.lunchmoney.dev/v2/migration-guide`. The real API reference page
(`https://alpha.lunchmoney.dev/v2/docs`) is a JS-rendered SPA that a text fetch can't read
— its content was cross-checked against the mock server's actual JSON instead, which is
stronger evidence (a real response body, not documentation prose) for every field-shape
claim below.

**Breaking changes applied:**

1. **Base URL**: `v1` → `v2` (`backend/services/aggregation/lunch_money.py::API_BASE`).
2. **Sign convention — FIXED, no longer a per-request setting.** v2: "positive amount
   values always indicate debit transactions and negative amount values always indicate
   credit transactions, regardless of the user's debits_as_negative setting." The
   `debit_as_negative` query param (which v1's `fetch_transactions` used to request, so the
   mapper's `amount > 0 → income` branch would match every sibling adapter) is REMOVED —
   confirmed live: passing it now returns `400`. `_txn_to_draft`'s branch is flipped:
   `amount < 0 → income`, `amount >= 0 → expense` (net-new positive-zero edge case is
   already excluded upstream by the existing `amount == 0` skip). Covered by a dedicated
   regression test (`test_txn_to_draft_v2_sign_convention_debit_is_expense_credit_is_income`)
   that feeds one known debit and one known credit through the real mapping function and
   asserts both the `kind` and which leg carries the amount — this is the exact class of
   silent money-direction corruption the original security review's finding L3 was
   written to catch, just on the opposite (sign, not NaN) axis.
3. **Field renames** (`_account_key`, `_txn_to_draft`, `map()`): `asset_id` →
   `manual_account_id`; `has_children` → `is_split_parent`; `parent_id` →
   `split_parent_id`; `is_group` → `is_group_parent`; `group_id` → `group_parent_id`.
   `tags`/`category_name`/`is_income`/status values (`cleared`→`reviewed`,
   `uncleared`→`unreviewed`)/the `pending`/`recurring`/`recurring_suggested` trio → a single
   `is_pending` boolean were all checked against the live mock response and confirmed
   **still unread** by this adapter both before and after the migration — no sibling
   adapter maps provider tags or categories either (see decision 2 above), and this
   connector never filtered by status/pending, so these renames are no-ops for us, not
   silent behavior changes.
4. **Removed hydrated properties — required a new lookup, not just a rename.** v2
   transactions arrive dehydrated: `category_name`/`category_group_id`/
   `category_group_name`/`is_income`/`asset_institution_name`/`asset_name`/
   `asset_display_name`/`plaid_account_name`/`plaid_account_display_name` are gone
   (confirmed empirically — none of these keys appear on a single transaction across a
   19-row live mock sample), and so is `display_name`, which the migration guide didn't
   even list. This adapter's account-naming (`_account_name` under v1) depended entirely on
   those inline fields. **New: `fetch_account_names()`** calls `GET /plaid_accounts` +
   `GET /manual_accounts` ONCE per sync (both small, unpaginated lists) and builds a
   `"plaid:<id>"`/`"asset:<id>"` → display-name lookup, stored on the adapter instance
   between `fetch()` and `map()`. This is the same "removed hydration → fetch the related
   object separately" pattern the task brief anticipated for categories — applied here to
   account names instead, since categories were already out of scope (decision 2) and
   never needed a lookup. Best-effort: a failure fetching either endpoint degrades to a
   generic `"Lunch Money account"` name rather than aborting the sync (a real auth failure
   already surfaces from the non-best-effort `fetch_transactions` call, which runs first).
   `description` (previously `display_name` → `payee` → `original_name`) drops the first,
   now-nonexistent tier: `payee` → `original_name`.
5. **Internal `account_map` keys deliberately UNCHANGED** despite the `asset_id` →
   `manual_account_id` rename: `_account_key` still mints `"asset:<id>"`, not
   `"manual:<id>"` — that prefix is this app's OWN namespacing, never Lunch Money's field
   name, so renaming it would force a data migration for any already-linked account with
   zero benefit (CLAUDE.md's "no backward-compat shims" is about not carrying two parallel
   READ paths for an old and new *data shape*; this is the opposite case — the on-disk
   shape doesn't need to change at all, so migrating it would be churn, not correctness).
6. **Group parent still skipped and still unverified; split parent is now excluded by v2
   by default.** Verified live: a baseline `GET /transactions` call (no `include_*` params)
   returns zero `is_split_parent` rows but DOES still return `is_group_parent` rows — so
   the split-parent half of the old "assumption" is now a server-enforced default rather
   than something this adapter has to defend against, while the group-parent half is
   unchanged (still returned by default, still an unverified-against-a-real-account
   assumption — the mock dataset used for this migration happens to carry zero rows with a
   matching `group_parent_id`, which is a property of the *demo* data, not evidence either
   way about real Lunch Money behavior). `_txn_to_draft`/`map()` still check
   `is_split_parent` defensively alongside `is_group_parent`, belt-and-suspenders, in case a
   real server's default ever differs from the mock's.
7. **An incidental correctness fix, not requested but worth recording.** v1's `_safe_get`
   only ever called `resp.raise_for_status()` — if Lunch Money v1 ever returned its
   documented `200 OK` with an error in the body (its own real behavior, confirmed live:
   `GET /v1/transactions` with a bad token returned a `{"name":"Error",...}` body), that
   response would have been silently treated as success, parsed as JSON, and
   `fetch_transactions` would have read `data.get("transactions")` as `None` → `[]` — an
   auth failure indistinguishable from "no transactions this window," never surfacing as
   `auth_error`. v2's "uses proper HTTP status codes instead" (its own migration guide's
   framing) closes this gap structurally, with no code change needed here — `raise_for_status()`
   now catches every Lunch Money error case on its own.
8. **Response status codes (`201`/`204` for POST/DELETE) and the group/split-as-separate-
   endpoints restructuring do not apply**: this connector is READ-ONLY (verified — every
   call in `lunch_money.py` is `httpx.get`; no `.post`/`.put`/`.delete` anywhere in the
   module), so it never observes those status codes or touches the group/split write
   endpoints in the first place.
9. **Pagination is UNCHANGED** — `offset`/`limit` + a boolean `has_more`, confirmed live
   against the v2 mock server; the existing 120-day rolling-window design and `_PAGE_LIMIT
   = 1000` needed no structural change, only the `debit_as_negative` param drop (#2 above).
   `_PAGE_LIMIT`'s true v2 ceiling could not be verified past 1000 (no real access token
   exists in this environment to probe past the mock server, which itself doesn't enforce
   one) — the previously-verified-safe value was kept rather than guessed higher.

**Deliberately NOT taken: `updated_since`-based incremental sync.** v2 adds
`created_since`/`updated_since` query params (confirmed live — both accepted, and
`start_date`/`end_date` are confirmed NOT required when using them), which could replace
the 120-day rolling-window rescan with a real cursor. Left out of THIS migration on
purpose: (a) it wouldn't change actual behavior — a transaction edited in Lunch Money after
import is still dropped by this adapter's external_ref dedup either way (create-only,
matching every sibling adapter, decision 5's consequences above), so the only benefit is a
smaller payload per poll, a pure efficiency win, not a correctness one; (b) a real cursor
needs its own design pass (cursor persistence in `LunchMoneyConfigStore`, interaction with
the `FetchResult.cursor` field the ingestion seam already exposes for exactly this purpose,
and a decision about what a *first* sync with no prior cursor should still bound itself to)
rather than a drive-by addition bundled into a breaking-change migration that already
touches sign convention, five field renames, and a new account-lookup call. A future
"Lunch Money incremental sync" task can pick this up as a standalone efficiency
improvement.

Verified again only against the v2 mock server + the real API's error/validation behavior
— no real Lunch Money account or access token exists in this environment, so (as before)
nothing here is proven against a real transaction round-trip; the group-parent
double-counting assumption in particular remains unverified against real account data both
before and after this migration.
