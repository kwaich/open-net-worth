# ADR — Assets tab: faithful port of the design handoff, backed by a new owned-assets domain

- **Status**: Accepted
- **Date**: 2026-09-06
- **Agent provenance**: Decided by the orchestrator driving the Assets-tab port from the Claude
  Design project (`Assets Tab.html` → `frontend/design_handoffs/assets_handoff/`, kept local and
  gitignored per the 2026-08-29 privacy commit). Owner instruction: "Implement `Assets Tab.html`"
  under the `port-design-handoff` skill — verbatim port, real-data seam, E2E incl. backend.

## Context

The handoff is a complete interactive prototype (`assets_tab/{data,register,detail,modals,main}.jsx`
+ `assets.css`, on the ONW design-system bundle) plus the "thinking + wireframes" doc that
motivates it. It introduces a NEW top-level tab — **Assets: things you own** (property, vehicles,
watches & jewellery, art, precious metals, cash-value policies, user-created categories) — whose
value is a **dated manual mark**, not a ledger balance and not a price feed.

Today the app has no such domain. A house or a car is an *account* of type
`real_estate|vehicle|collectible|other_asset` whose balance is a ledger fold: no cost basis, no
valuation date, no cadence, no loan link, no photo/documents, and a `vehicle` with no
`asset_class` override is counted as **liquid cash** in reachability (`portfolio_rows.py:33,62`).
The prototype's summary strip and its Cash / Investments "class cards" mirror the Overview
figures; its loan picker lists real liability accounts; disposal posts real proceeds.

## Decisions

1. **Faithful port, not re-derive.** The prototype JSX → `frontend/src/components/tabs/assets/`
   (`Assets.tsx` shell, `register.tsx`, `detail.tsx`, `modals.tsx`, `assetsData.ts` engine,
   `icons.tsx`, `assets.css`), same DOM and `as-*` class vocabulary, pixel-identical. Stripped
   only: the standalone shell (`html/body`, `AppNav`, `.onw-*`), the `:root` token override block
   (the app's generated tokens already collapse `--surface` onto `--bg` and define `--hover`,
   `--selected`, `--lucius`; the `--cat-*` aliases are the one genuinely new set and move to
   `src/design/tokens.source.mjs`), `.as-page`'s own gutter (`.app-layout` owns it), and the
   `.mask` privacy class (→ the app's `.money` / `body.hide-amounts` convention, as Commitments).
   Classes the prototype defines but never mounts (`AssetTable`, `FlowsTable`, `ReviewModal`,
   `.mk-density`, `.as-cls-lines`, …) are NOT ported — the dead-class ratchet is at zero and the
   spec is what `main.jsx` renders. Recorded in `PARITY_AND_GAPS.md`.
2. **New tab id `assets`, route `/assets`, nav slot after Accounts.** `?id=<asset>` deep-links the
   detail view (the Commitments `?open=` precedent: push on open, replace on close). The tab is in
   `SELF_HEADER_TABS` and renders its own `PageHeader`. `Container size="wide"`; the grid needs the
   reading-band cap gone, so `assets` joins the `app-layout--wide` clause.
3. **A new domain, not a better account type.** Table `owned_assets` (+ `owned_asset_categories`
   for user-created categories), both `book_payload_table`s; store `db/owned_assets_store.py`;
   service `services/owned_assets.py` with ONE write choke `apply_owned_asset_change(change)`
   (ops: `create | update | add_mark | dispose | delete | set_photo | add_doc | update_doc |
   remove_doc | create_category`); router `api/assets_api.py` under `/api/assets`. Name chosen
   to avoid the existing `asset_*` (instrument-detail / Intel) vocabulary.
4. **Value is the newest mark; identity edits never touch marks.** `marks[]` are ordered child
   rows (the `installments[]` shape) — a new mark is prepended, never overwritten. Creating with a
   purchase price also writes the purchase mark dated `cost_date` (exactly `addAsset` in the
   prototype). `update` cannot carry `value`. Freshness (`fresh|due|stale|auto`) is derived
   client-side from `months_old / cadence_months`, verbatim `freshness()`.
5. **Liabilities stay accounts; assets point at them. Equity is derived, never stored.**
   `loan_account_id` references a liability account; the API projects `loans[]` with the
   ledger-computed balance (`abs(balance_base)`) plus display-only `apr`/`payoff_date`.
6. **Net worth seam — three folds, one helper, history valued as-of.**
   `services/owned_assets_valuation.py` (imports only the store + `core.fx`, so `ledger.py` can
   import it without a cycle) exposes `values_by_band(base, *, as_of=None)`: share-adjusted
   (`value × share_pct/100`), base-converted, archived/disposed excluded (from `disposed_on`),
   banded `property → "property"`, `metal → "commodity"`, everything else → `"other_asset"`,
   reachability `illiquid`. Wired into (a) `ledger.net_worth()` (`account_classes[band]`, so
   `breakdown.account_classes` and `net_worth` move together), (b) `ledger._breakdown_as_of()`
   via a `manual_assets_base` parameter — and `net_worth_history` computes it PER POINT with
   `as_of=<point date>` using the newest mark on/before that date (a manual asset's marks ARE its
   price series; this is the honest version, not the options-style today-only back-projection),
   (c) `portfolio_rows.projected_rows()` gains `_asset_as_row` so allocation / reach / KPIs /
   gains pick assets up for free. Read caches invalidate through the payload store's write hook.
7. **Summary strip and class cards are server money math.** `GET /api/assets` returns a
   `summary` block (own / owe / net worth / unrealized, and the `cash` / `investments` class
   cards) composed from `ledger.net_worth()` + `portfolio_analytics` — the prototype's
   `worthTotals()`/`CLASSES` constants are replaced by it. Per-asset display arithmetic
   (equity, gain, gain %, years held, annualised) stays client-side verbatim for fidelity: pure
   subtraction over server numbers.
8. **Disposal is ONE DB transaction.** `dispose` posts a single-leg `kind="investment"` inflow
   into the chosen cash account through `ledger_service.create_transaction(..., conn=conn)` under
   `locked_unit_of_work(owned_assets, transactions, txn_audit)`, with
   `external_refs=[{source:"asset_disposal", ref:<asset_id>}]` for idempotency, then archives
   the asset (`archived`, `disposed_on`, `disposal_proceeds`, `disposal_txn_id`). History kept.
   `delete` ("Remove from Assets") is a hard delete for things that should never have existed.
9. **Photos and documents are attachments referenced from the payload.** Uploads go through
   `attachment_ingress.persist_inbound_file`; the asset stores `photo_ref` and
   `docs[] {ref_id, name, added_on, lucius_readable}`. Served by the existing
   `/api/uploads/{ref}/image|file` (cookie auth, so `<img src>` works). A dangling ref renders
   as the category icon. `lucius_readable` is stored and honoured by the asset read tools.
10. **Review reminders are Automations.** `automate=true` creates ONE system automation per
    asset (`create_system_automation(..., system_key="asset_remark:<id>")`, rrule
    `FREQ=MONTHLY;INTERVAL=<cadence>`), stored as `automation_id`; the rail shows
    `next_run_at` as "next Oct 2026" and "Manage ›" navigates to Automations. `automate=false`
    deletes it. Cadence edits update the trigger.
11. **Lucius parity is a hard gate.** `ai/tools/assets.py`: `list_assets`, `get_asset` (read),
    `create_asset`, `update_asset`, `record_asset_value`, `dispose_asset`, `delete_asset`,
    `create_asset_category` (`mutates=("assets",)`; disposal/delete confirm-gated by the
    dispatch policy). The "Describe it to Lucius" door is `onAskLucius('Add an asset: ')` — the
    real dock prefill, no local chat. An `asset` mention descriptor is registered.
12. **Portability.** `assets.json` / `asset_categories.json` join `PORTABLE_FILES`, the
    `BRIDGED` map and the reset wipe map. Backups need nothing.
13. **Existing property/vehicle ACCOUNTS are not migrated.** The wireframes list this under
    "would ask before building"; the hi-fi does not cover it. Documented as an owner decision in
    `PARITY_AND_GAPS.md` with the double-count caveat.
14. **Pre-existing regression fixed in the same change:** `RouterShell.openLucius` recursed into
    itself on desktop (commit 686b29684) so every tab's "Ask Lucius" stack-overflowed; the
    desktop branch is restored to `setChatOpen(true)`.

## Wire contract (`GET /api/assets`)

```
{ assets: Asset[], categories: Category[], loans: Loan[], cash_accounts: {id,name,balance_base}[],
  summary: Summary, base_currency }

Asset { id "oa_<12hex>", name, category, description, photo_ref|null, facts [[label,value]],
        notes, cost|null, cost_date ISO|null, share_pct (100), share_note, loan_account_id|null,
        cadence_months (3|6|12|24), automation {id,label,next_run_on}|null, currency,
        value, valued_on ISO, months_old, marks [{id,date,value,source,by:"you"|"automation",created_at}] newest first,
        docs [{ref_id,name,added_on,lucius_readable}], archived, disposed_on|null,
        disposal_proceeds|null, disposal_txn_id|null, disposal_account_id|null, created_at, updated_at }
Category { key, label, plural, color "var(--cat-…)", cadence_months, custom, hint? }
Loan { id, name, balance (positive, base), rate_label "3.1%"|"", term_label "to 2044"|"" }
Summary { own, owe, net_worth, unrealized,
          classes: { cash: {value, sub, yield_label|null, gain:null, gain_pct:null, as_of:"today", count_label},
                     investments: {value, sub, gain, gain_pct, as_of:"today", count_label} } }
```
Writes: `POST /api/assets`, `PATCH /api/assets/{id}`, `DELETE /api/assets/{id}`,
`POST /api/assets/{id}/marks {value, source?, date?}`, `POST /api/assets/{id}/dispose
{proceeds, account_id, date?}`, `POST /api/assets/{id}/photo` (multipart `file`),
`POST /api/assets/{id}/docs` (multipart `file`), `PATCH|DELETE /api/assets/{id}/docs/{ref_id}`,
`POST /api/assets/categories {label}`. Every write returns the refreshed asset. Non-archived
assets only in `assets[]` unless `?include_archived=1`.

## Intentional deviations (handoff-sanctioned or demo harness)

- `AppNav`, the `Lucius Tab.html` redirect, `loremflickr` photo URLs and `photos/*.png`
  fallbacks — prototype harness; not ported.
- `AssetTable`, `FlowsTable` (carry / income), `ReviewModal` + `suggestion` — defined but not
  mounted by `main.jsx`; not ported. Listed as follow-ups in `PARITY_AND_GAPS.md`.
- Mark `t` (mock years) → derived from real ISO dates; `cost_date` / "Bought on" accepts
  "Mar 2019" and is stored as an ISO first-of-month.
- The detail rail's "Automate this review" button had no handler in the prototype; it is wired
  to Decision 10.

## Consequences

- Net worth, history, allocation and reachability now include manually valued things, banded as
  property / commodity / other and illiquid — a correctness gain over the account workaround.
- Two payload tables, one Alembic revision (`202609061000_owned_assets`), three fold sites in
  `ledger.py`, one row projector, one mention type, three portability registrations.
- `tests/test_owned_assets_{store,service,api,tools}.py` on `db_isolated`; frontend vitest per
  ported component; acceptance file `docs/qa/acceptance/assets.md`.

## Update (2026-09-06b — backend wiring lane closed Decisions 6, 7, 11, 12)

A second lane (scoped to `services/ledger.py`, `services/portfolio_rows.py`, `api/assets_api.py`
(`GET /api/assets/{asset_id}` only), `ai/tools/assets.py`, `ai/tools/_write_classification.py`,
`ai/tools/_bounded.py`, `services/mentions/descriptors/new_types.py`, `core/paths.py`,
`db/portable_bridge.py`, `core/portable.py` — explicitly NOT the four domain-lane files
(`services/owned_assets.py`, `services/owned_assets_valuation.py`, `db/owned_assets_store.py`,
`api/assets_api.py`'s existing routes), which shipped as the domain in the base commit) closed
the four Decisions the domain lane deliberately deferred:

- **Decision 6 (net-worth seam) is DONE**, exactly as specified: `net_worth()` folds
  `values_by_band(base, rates=rates)` into `account_classes[band]` and reports
  `breakdown.owned_assets`; `_breakdown_as_of` gained `manual_assets_base`; `net_worth_bands()`
  passes today's `values_by_band(base)`; `net_worth_history()` computes it PER POINT with
  `as_of=<point date>`. `portfolio_rows.projected_rows()` gained `_asset_as_row` — the one design
  call made mid-implementation: `quantity` is the share-adjusted BASE value with `price=1` (so
  `base_value == quantity`, matching the ADR's literal wording), but `cost_basis` could NOT
  honestly be "cost × share_pct/100" as written — the shared `_project` fold multiplies
  `cost_basis * quantity` to get `cost_base`, and with `quantity` already a base-dollar amount
  that would square the value. `cost_basis` is instead the share-adjusted, base-converted cost
  divided by `quantity`, which reconciles back to the honest total when `_project` multiplies it
  out. Read-cache invalidation needed no new plumbing: `PRAGMA data_version` is DB-wide, so any
  owned-asset write already invalidates `net_worth`/`net_worth_history`'s `read_cache.cached`.
- **Decision 7 (`GET /api/assets/{asset_id}`) is DONE** — registered after the literal
  `/api/assets/categories` route, 404 via `NotFoundError`.
- **Decision 11 (Lucius parity) is DONE**: `ai/tools/assets.py` ships all eight named tools
  (`list_assets`, `get_asset`, `create_asset`, `update_asset`, `record_asset_value`,
  `dispose_asset`, `delete_asset`, `create_asset_category`), each calling
  `services.owned_assets` only. `list_assets` bounds rows via `_bounded.DEFAULT_ASSET_LIMIT` and
  summarizes `docs[]` to `{name, lucius_readable}` only (never `ref_id`, reserved for
  `get_asset`'s full detail). `dispose_asset` is tagged `mutates=("assets", "ledger")` — it
  posts a real transaction, so it carries both domains, mirroring `income.py`'s equity/ledger
  tools rather than a single-domain tag. The `asset` mention descriptor was added the same lane
  (`services/mentions/descriptors/new_types.py`, order=35) over the service's `list_assets()`
  projection. `frontend/src/hooks/serverState/aggregates.ts`'s `useCommitmentsSummary` list
  gained `'assets'` (the one permitted frontend touch, a plain string list) — NOT
  `useNetWorth.ts`'s or `usePortfolioAnalytics`'s own `useDataVersion` lists, which are the
  hooks that actually read the two endpoints this lane changed; wiring those is a flagged
  frontend follow-up, out of this lane's allowed scope.
- **Decision 12 (portability) is DONE**: `assets.json`/`asset_categories.json` joined
  `PORTABLE_FILES`, `portable_bridge.BRIDGED` and the reset wipe map, both via
  `_planning_bridge` (mirroring `commitments.json`). One fix mid-implementation:
  `_planning_bridge` gained an `id_field` parameter (mirroring `_misc_bridge`'s own) because
  `OwnedAssetCategoriesStore` keys its payload by `"key"`, not `"id"` — the generic bridge's
  default `id_field="id"` silently inserted `id=NULL` and the categories bridge failed its own
  round-trip test before this was caught.
- `tests/test_owned_assets_networth.py`, `tests/test_owned_assets_tools.py`,
  `tests/test_owned_assets_mentions.py`, `tests/test_owned_assets_portability.py` (new);
  `tests/test_owned_assets_api.py` gained the `GET /api/assets/{asset_id}` cases;
  `tests/db/test_portability_db_bridge.py`'s exhaustive `BRIDGED` set and
  `tests/test_mention_type_frontend_coverage.py`'s `EXEMPT` map (the Assets tab UI predates this
  descriptor, so no frontend source yet stamps `data-entity-id` for it) were updated to match.

## Update (2026-09-06c — security-review hardening lane)

A third lane, scoped to `services/owned_assets.py`, `api/assets_api.py`, `ai/tools/assets.py`,
`ai/tools/_bounded.py` and `main.py`'s validation-error handler, closed a live-probed security
review of the owned-assets surface (C1, H1–H3, M1–M3, L1–L2). None of these change the wire
contract above except where noted.

- **C1 (the "re-mark" reminder self-escalated an unattended automation) is CLOSED.**
  `_set_automation`'s spec is now fully STATIC — `name`/`instructions` reference the asset by
  its opaque id only, never its (fully user-controlled) `name` — and pins
  `"permission_mode": "manual"` explicitly, since `automations.service.validate_automation`
  defaults an unspecified mode to `"auto"` (a scheduled reminder has no latency requirement
  that justifies skipping the human). `create_asset`/`update_asset` (the Lucius tools) build
  their change dict from an EXPLICIT field allow-list (`_CREATE_FIELDS` / `_UPDATE_FIELDS`)
  that excludes `automate` — the HTTP API keeps it. One correction made mid-fix: the finding
  as written assumed a call carrying `automate` would be "rejected by the extra='forbid'
  schema" at dispatch; measured against `ai/tools/registry.py`'s own documented design, this
  is false — `registry.dispatch`'s `validation_error()` deliberately does NOT enforce
  `additionalProperties` for ANY tool (the propose→apply replay paths depend on the
  laxity — see that module's S2 note), and no other layer runs `CreateAssetInput`/
  `UpdateAssetInput` as an actual pydantic gate; the schema only shapes what an honest model
  is TOLD it may pass. The real, load-bearing gate is therefore the handler's own allow-list,
  not schema removal — schema removal alone stops an honest model from offering `automate`,
  the allow-list stops it from ever reaching `apply_owned_asset_change` regardless of how it
  arrived in `input_data`.
- **H1 (NaN/Infinity) is CLOSED** — `math.isfinite` guards in the choke (`value`, `cost`,
  `share_pct`, mark `value`, dispose `proceeds`) plus `Field(allow_inf_nan=False)` on
  `AssetCreate`/`MarkCreate`/`DisposeBody`. One bug found by the API-level test itself and
  fixed in the SAME lane: FastAPI's default `RequestValidationError` handling echoes the
  raw rejected value back in the error body, and Starlette's `JSONResponse.render` calls
  `json.dumps(..., allow_nan=False)` — a real (non-string) `nan`/`inf` there crashes with an
  unhandled `ValueError`, turning the intended 422 into a 500. `main.py`'s existing
  `log_validation_error` handler (previously a pass-through to FastAPI's default) now
  sanitizes any non-finite float in the error tree before responding. This is a correctness
  fix for the WHOLE app's validation-error path, not owned-assets-specific — before this
  lane, no field anywhere used `allow_inf_nan=False`, so no request could previously trigger
  it.
- **H2 (get_asset leaked a withheld doc's ref_id) is CLOSED** — `ai/tools/assets.py`'s
  `_project_asset` withholds `ref_id` (and every other field but `name`/`lucius_readable`)
  for a doc marked `lucius_readable=false`, applied to `get_asset` and to the asset every
  write handler returns; `marks` is capped to the newest 50 with `marks_total` (new
  `_bounded.DEFAULT_ASSET_MARK_LIMIT`). `list_assets`' doc summary was already ref_id-free
  and is unchanged.
- **H3 (dispose ignored the ledger's own idempotency no-op) is CLOSED** — `dispose` now reads
  `meta["idempotent_existing_id"]` off `ledger.create_transaction` and, when set, returns the
  asset UNCHANGED instead of calling `store.archive` with this call's own inputs. The
  previous lane's archived-asset same-inputs check only covers a SEQUENTIAL retry (a second
  call always re-reads `archived=True` fresh); this closes the narrower, genuine race — two
  concurrent dispose calls both read the asset as not-yet-archived before either commits, and
  the loser's `create_transaction` call finds the winner's row already booked.
- **M1 (dispose relabelled a base-currency figure as the destination account's currency) is
  CLOSED** — `cash_accounts()` filters to non-archived cash accounts in the BOOK'S BASE
  currency only (an owned asset's `currency` is always base at create time), and `dispose`
  additionally checks the resolved account's currency against the asset's own as defense in
  depth.
- **M2 (dates unvalidated) is CLOSED** — `cost_date`, mark `date` and dispose `date` parse via
  `date.fromisoformat`, with `YYYY-MM` normalised to the first of the month (matching the
  UI's own storage convention); a mark/dispose date after today is refused. `cost_date` is
  deliberately NOT future-refused — a purchase date has no "future" case to defend against.
- **M3 (no length bounds) is CLOSED** — `name`/`description`/`notes`/`share_note`/fact
  label·value/category `label`/mark `source` are bounded both in the choke
  (`services.owned_assets`'s `MAX_*` constants, the single source of truth the API models
  import) and on the wire (`Field(max_length=...)` plus a shared `facts` validator).
- **L1/L2 (upload ordering, non-image `/photo`) are CLOSED** — both upload routes resolve the
  asset (404) BEFORE `persist_inbound_file`, and check `UploadFile.size` against the 25MB cap
  before `await file.read()` when the multipart parser already populated it. `/photo` requires
  `result["kind"] == "image"` (415 otherwise), deleting the attachment it just persisted so a
  rejected non-image never survives as an orphan row.
- `tests/test_owned_assets_service.py`, `tests/test_owned_assets_api.py` and
  `tests/test_owned_assets_tools.py` each gained a dedicated section per finding (regression
  test per bug fix, `write-test` skill); `tests/golden/tool_input_schemas.json` lost
  `create_asset`/`update_asset`'s `automate` property.

## Update (2026-09-26 — create/edit redesign)

Amended, not rewritten: **Decision 1** (faithful port) no longer governs the Add/Edit surfaces —
see ADR `2026-09-26-assets-stepped-create-and-section-editing` (four-step create; editing by
section on the asset page). **Decision 4**'s "values are never overwritten" becomes "recording
appends, correcting replaces and marks the value edited, removing deletes"; "identity edits never
write marks" stands — see ADR `2026-09-26-owned-asset-purchase-point-is-derived`, which also makes
the purchase point derived. **Decision 7**: the register's composition rail is now server money
math (`summary.composition`), and each asset carries a server-computed `share_value`.
