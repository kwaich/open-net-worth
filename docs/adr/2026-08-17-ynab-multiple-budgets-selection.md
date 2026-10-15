---
title: YNAB connector — /plans not /budgets, multiple-budget selection, milliunits, delta sync
status: Accepted
date: 2026-08-17
deciders: repo owner (task brief) + implementing Claude agent (ynab-connector lane)
---

# YNAB connector — API shape and multiple-budget selection

## Context

YNAB (You Need A Budget, api.ynab.com) is a third-party budgeting SaaS: a user who already
tracks spending there can bring that data into Open Net Worth's own ledger via a
self-service personal access token, the same shape as SimpleFIN's access URL / Lunch
Money's access token. The task brief asked for the closest existing precedents (SimpleFIN,
then the freshly-shipped Lunch Money connector) to be read and followed structurally, and
explicitly flagged its own endpoint-path summary as possibly imprecise, asking for live
verification before building.

The brief also named one structural difference this connector has to resolve that neither
precedent does: a YNAB personal access token can see **multiple budgets** (SimpleFIN/Lunch
Money each have exactly one implicit account universe per credential), and asked for a
judgment call on how the app should handle that, matching an existing "pick one of several"
precedent elsewhere in the app if one exists.

## API shape actually built against

Verified live this session against YNAB's own current OpenAPI spec,
`https://api.ynab.com/papi/open_api_spec.yaml` (`info.version: 1.86.0`) — fetched directly,
not summarized from a docs page — plus a live unauthenticated probe of `api.ynab.com`:

- **The real, current, documented base path is `/plans`, not `/budgets`.** The spec defines
  `GET /v1/plans`, `GET /v1/plans/{plan_id}/accounts`, `GET
  /v1/plans/{plan_id}/transactions`, `GET /v1/plans/{plan_id}/categories`, etc. — zero
  `/budgets` paths anywhere in the spec. This directly contradicts the task brief's assumed
  `/budgets/{budget_id}` shape, exactly the kind of discrepancy the brief asked to be
  verified and corrected. YNAB's own docs prose (not the spec) separately claims the old
  `/budgets/{budget_id}` paths "continue to work... for backward compatibility, but are no
  longer documented" — that claim could NOT be independently confirmed from this
  environment (both `/v1/plans` and `/v1/budgets` return an identical `401 Unauthorized`
  with no real token to distinguish "exists, needs auth" from "doesn't exist, gateway 401s
  first"), so **this connector is built entirely against the current, documented `/plans`
  surface and never references the undocumented alias.**
- Auth: `Authorization: Bearer <token>` — matches the brief, confirmed in the spec's
  `security: [{bearer: []}]` and live (`{"error":{"id":"401","name":"unauthorized",...}}`).
- Rate limit: 200 requests/hour per token, rolling window — confirmed via a live fetch of
  the docs page prose (not present in the OpenAPI spec itself).
- Amounts: **milliunits**, a signed int64 where 1000 = one currency unit. Verified via the
  spec's own worked example on the `import_id` field: a transaction "in the amount of
  -$294.23 USD" has `import_id` `'YNAB:-294230:2015-12-30:1'` — i.e. `-294230 / 1000 ==
  -294.23`. Sign convention (negative = outflow, positive = inflow) already matches every
  other adapter in this codebase, so — unlike Lunch Money's `debit_as_negative` request flag
  — no sign transform is needed.
- Dedup id: every transaction carries a required `id`, stable and unique within the budget.
  The separate `import_id` field is optional (present only for transactions YNAB itself
  imported) and exists for YNAB's OWN direct-import dedup, not a universal identifier — so
  `id`, not `import_id`, is what this connector dedupes external_refs on.
- Delta sync: `last_knowledge_of_server` is real, documented on every list endpoint
  (`GET /plans/{plan_id}/transactions` included), and returns only entities changed since a
  previously-returned `server_knowledge` value. The task brief asked for this to be used if
  it could be confirmed well-documented, since it is a genuine improvement over
  SimpleFIN's/Lunch Money's simpler full-refetch-a-window pattern — confirmed, and used as
  the sync cursor (`YnabConfigStore.server_knowledge`). The very first sync (no stored
  cursor) omits both `last_knowledge_of_server` and `since_date`, letting the API apply its
  own documented default (the trailing 1 year) instead of this module re-implementing that
  window.

## Decision: multiple budgets — auto-detect-if-one, explicit pick otherwise, never guess

**Considered:**

1. **Always require an explicit `budget_id`.** Simple and unambiguous, but a needless extra
   step for the overwhelmingly common case (a personal YNAB user with exactly one budget) —
   friction with no safety benefit when there is nothing to disambiguate.
2. **Always auto-pick the first/most-recently-modified budget when none is set.** Fails
   exactly the case that matters: a token with two or more budgets (a personal + a shared
   "family" budget is a completely ordinary YNAB setup) would silently import into whichever
   one happened to sort first — wrong money landing in the ledger with no error, no warning,
   nothing to notice until the numbers look off. This app already has a strong, explicit
   precedent against this shape: `resolve_account_ref` and `resolve_category_ref`
   (CLAUDE.md's ledger invariants) both **RAISE on ambiguity, naming every candidate**,
   rather than picking one. `services/aggregation/providers.py`'s `resolve()` does the same
   for an unknown/ambiguous provider key (`UnknownProvider`, "list every bad key at once").
   Silently picking a budget would be the one place in this connector that broke that
   pattern for the sake of convenience, on the exact axis (which container gets the money)
   where being wrong is worst.
3. **Auto-detect + persist when the token sees exactly one budget; otherwise refuse and name
   every real candidate.** Chosen. Zero friction for the common single-budget case (which
   this app's own precedent — `SearchConfigStore`'s single active web-search provider is
   auto-selected when nothing else is configured — treats as a reasonable default shape when
   there is genuinely nothing to choose between), and the ambiguous multi-budget case is
   handled the SAME way every other ambiguity in this codebase is: raise
   (`YnabBudgetSelectionRequired`, a `ValidationError` subtype), name every real candidate by
   name and id, and require an explicit pick — never a silent guess.

**Implementation**, on top of (3):

- A dedicated picker: `GET /api/ynab/budgets` lists every budget the saved token can see
  (mirrors the closest existing precedent for this shape in the app, Enable Banking's
  `GET /api/enable-banking/institutions` bank picker), so the config form can offer a real
  dropdown instead of asking the operator to find and paste a raw budget id.
- Picking a budget through `PUT /api/ynab/config` is **live-validated** against the token
  before it is saved (`resolve_budget`), mirroring SimpleFIN's `claim_setup_token`: a
  typo'd or copy-pasted-wrong id surfaces immediately as a clean error, not silently on the
  next scheduled sync.
- A blank `budget_id` on that same endpoint explicitly **clears** the pick (falls back to
  auto-detect-if-there-is-only-one on the next sync) — the one field in `YnabConfigStore`
  where a blank value means something other than "leave unchanged", called out in the
  store's own docstring since it deviates from the write-only-secret convention every other
  field in this store (and every sibling connector's credential field) follows.
- Switching the resolved `budget_id` (auto-detected, explicitly picked, or cleared) always
  resets `server_knowledge` (the delta cursor) and `account_map` in the SAME store update —
  both are scoped to the PREVIOUS budget's data, and carrying either forward across a budget
  switch would silently corrupt the next sync (a stale cursor skipping the new budget's real
  history; a stale account map pointing YNAB account ids from one budget at Open Net Worth
  accounts that belong to a different one).
- A sync that hits the ambiguous case (0 or 2+ budgets, none picked) comes back with
  `status: "needs_budget_selection"` and a `last_error` naming every real candidate — never
  a call failure, matching every sibling connector's "read the status field" contract.

## Other decisions (matching precedent, no deviation)

- **No category mapping** — matches the Lunch Money precedent exactly (see
  `docs/adr/2026-08-16-lunch-money-connector-scope.md` §2). YNAB's own `category_id` is the
  user's own free-form taxonomy inside YNAB; this app's `category` is an opaque id that
  raises on an unknown value. Imported transactions carry `category=None` (Unsorted).
- **Transfers are not special-cased** — matches SimpleFIN/Lunch Money exactly. A transfer
  between two YNAB accounts arrives as two ordinary rows (one per account); each is mapped
  as its own income/expense leg against its own auto-created account, the same shape every
  sibling adapter already produces for a transfer between two of a user's own aggregated
  accounts.
- **Account kind/type: always `kind="asset"`, `type="bank_transfer"`** on auto-create, even
  though YNAB's own account payload carries a real `type` (checking/savings/creditCard/…)
  that could inform a smarter mapping. Matches SimpleFIN/Lunch Money's exact precedent
  (their own comments: "default to a bank-style asset and let the user re-classify in the
  UI... a negative balance on a card/loan account stays raw-negative") rather than inventing
  new classification logic this connector's siblings don't have.
- **Poll interval: 21600s (6h) default, 3600s (1h) floor** — byte-identical to
  SimpleFIN/Plaid/Lunch Money/Enable Banking. The rate-limit math (see "API shape" above)
  leaves enormous headroom at either number, so there is no reason to pick something
  different from the established convention.

## Consequences

- A personal (single-budget) YNAB user connects with just a token — no extra step. A
  multi-budget user is asked, once, to pick which budget feeds this app's ledger, and can
  change that pick later (which safely resets the budget-scoped cursor/account map).
- A transaction edited or categorized in YNAB after it was already imported here is not
  reflected — matches every sibling adapter's create-only behavior.
- YNAB's `/budgets` compatibility alias (if real) is deliberately unused; if YNAB ever
  actually removes it, nothing here is affected, since it was never referenced.

## Agent provenance

Implemented on `feature/ynab-connector` (worktree `.claude/worktrees/ynab`), off `main`.
The task brief asked for judgment on the multiple-budget question specifically and asked
for the `/budgets` vs `/plans` path to be verified rather than trusted from a possibly
imprecise summary — both are recorded here because neither is visible from the diff alone.
Verified against mocked HTTP responses matching the live-verified API shape (the OpenAPI
spec fetched directly from `api.ynab.com`) — no real YNAB account or access token exists in
this environment, so nothing here is proven against a real round-trip.
