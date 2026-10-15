---
title: Remove the tax feature outright (supersedes the 2026-07-22 hide)
status: Superseded
date: 2026-08-25
deciders: Claude, acting on an explicit owner directive
supersedes: docs/adr/2026-07-22-hide-tax-tab-and-tools.md
superseded_by: docs/adr/2026-09-21-remove-category-taxability.md
---

# Remove the tax feature outright (supersedes the 2026-07-22 hide)

## Context

On 2026-07-22 the Tax tab and its three AI tools were **hidden, not deleted** — the reasoning
being that the owner said "hide," that hiding was trivially reversible, and that CLAUDE.md's
Alpaca precedent framed deletion as appropriate only for a feature actually being retired.

The owner has now made that call: *"we are not providing any tax features atm, it will be built
later."* A feature that is not being provided has no reason to keep a live REST surface, a
71KB service, a jurisdiction-ruleset data directory, and ~15 test files in the tree. Git history
is the reversibility mechanism for a rebuild; `_CATALOG_HIDDEN` was never meant to hold a
feature indefinitely.

The scope question was the whole decision here, because "tax" in this codebase spans three very
different things:

1. **The tax feature** — the `/tax` tab, `services/tax.py`, `tax_jurisdictions.py`, `tlh.py`,
   `api/tax.py`, `api/tlh.py`, the tax AI tools, the shipped rulesets, `profile.tax_config`.
2. **Taxable-income tagging** — `transactions.taxable` (tri-state), `categories.taxable_default`,
   `POST /api/categories/{id}/taxable`, the Plan "Taxable / Tax-free" pill, `taxable_received`
   /`taxable_ytd` in the income summary.
3. **Tax-adjacent recorded facts** — `income_events.tax_withheld` (a broker fact; `net = gross −
   tax`), `grants.tax_event` (`at_vest`/`deferred`, which decides whether a vest posts an income
   transaction), tax lots (cost basis), and `profile.tax_residency_country`.

## Decision

**Delete (1). Keep (2) and (3).**

Tiers 2 and 3 are not the tax feature; they are user tagging and recorded facts that the feature
*read*. `tax_withheld` is a number off a dividend statement — deleting it breaks income math.
`tax_event` governs a write path. Tax lots are cost basis. Each would need a data migration and
would remove working behaviour, for no gain, and each is what a future tax engine will read.

### Deleted

- **Backend:** `api/tax.py`, `api/tlh.py` (+ both router registrations), `services/tax.py`,
  `services/tax_jurisdictions.py`, `services/tlh.py`, `ai/tools/tax.py`, `ai/tools/tlh.py`
  (+ registry entries), `backend/data/tax_jurisdictions/`, `core.paths.TAX_JURISDICTIONS_DIR`,
  `models/profile.TaxConfig` + both `tax_config` fields.
- **Frontend:** `tabs/Tax.tsx`, `tabs/tax/`, `shared/TaxReportPanel.tsx`,
  `shared/TaxLossHarvestPanel.tsx`, the `/tax` route + `tabs.ts` entry + `RouterShell` branch,
  and ~270 lines of now-dead CSS (the dead-class ratchet in `check-css-classes.mjs` makes
  leaving it a build failure, not a cosmetic debt).
- **Tests/QA:** 15 backend test files, 7 browser-use specs, `docs/qa/acceptance/tax.md`.
- **Migration 0058** drops `profile.tax_config` from every payload — with `services/tax.py`
  gone it is unreadable data.

### Kept, with the tax stripped out

`services/equity_report.py` was listed for deletion and is NOT deleted. It is ~90% a **vest
report** (per-vest rows: symbol, dates, units, FMV, gross gain) and ~10% tax
(`estimated_tax_base`, `income_rate_pct`, `jurisdiction_note`). It also backs a live
EquitySubTab panel and is the documented discovery path for `vest_event_id`
(`ai/tools/equity.py` tells the model to "use get_equity_report or list_grants"). Deleting it
would have removed a working non-tax capability and broken the vest-undo flow's only discovery
route. The three tax fields and both profile-reading helpers are gone; the tool keeps its name
(`get_equity_report`) so no alias is needed, and the frontend component is renamed
`EquityTaxReport` → `EquityVestReport`.

`grants.tax_event` survives inside it: a `deferred` grant is excluded from the total because no
income transaction is posted at vest — a ledger fact, not a tax verdict.

### `profile.tax_residencies` is NOT a leftover — it is deliberate

Migration 0060 (a separate lane, same day) listified the profile identity fields and added
**`tax_residencies`** as a new one, alongside `countries_of_residence`. That is a tax-named
field arriving in the same window this ADR removes the tax feature, so to be explicit: it
**stays, by the owner's decision (2026-08-26)**. Where a person is tax-resident is a personal
fact — one they may hold several of, independently of where they live — and it is read by
Lucius's user-context block (`ai/prompts.py`) like any other profile field. It is not part of
the tax engine, has no dependency on anything this ADR deleted, and is precisely the kind of
input a rebuilt engine would need. Do not remove it as a straggler of this cleanup.

### `_CATALOG_HIDDEN` is emptied, not removed

The seam stays (it predates the tax hide and was built for exactly this shape of problem), but
its three tests depended on the live set being non-empty. They now **inject** a hidden name via
`monkeypatch` instead, so the filter is still exercised on all three paths it must hold on —
`build_tool_catalog()`, `find_tools`/`_candidate_schemas`, and `offer_tool_names()` (the
explicit-agent-offer path the 2026-07-22b review found unguarded). Deleting those tests along
with the tools would have silently retired a real regression guard.

### Two collateral renames

- `ExtensionDomain.tax_year` → `year` (+ the registry's ceiling-matching and the ExtensionsPanel
  `TY…` chip). The extension registry is a generic pack system whose *worked example* was tax;
  no shipped pack uses the field, so this is contained.
- `KNOWN_DATA_PACK_ENGINES` drops `"tax_jurisdiction"` — that engine no longer has an
  implementation, and the set is validated against.

## Consequences

### Positive
- No dead REST surface: `GET /api/tax-report`, `/api/tax/estimate`, `/api/tax-loss-harvest`
  were live and callable throughout the hide. They are gone.
- Lucius cannot describe tax figures by any path, including the agent-offer path — not because
  three names are filtered, but because the tools do not exist.
- ~4,000 lines removed from the tree the hardening loop has to reason about.

### Negative / accepted
- Rebuilding means recovering from git history rather than emptying a frozenset. That is the
  accepted cost of the owner's call, and the PRD (`docs/prd/tax-handling.md`) and design RFCs
  (`.claude/rfcs/phase4/`) are deliberately left in place for that rebuild.
- `docs/prd/tax-handling.md` and the phase-4 RFCs now describe a feature that does not exist.
  They are planning artifacts, not current truth, and `docs/DATA_MODEL.md` /
  `docs/ai/TOOL_CATALOG.md` / `docs/launch/BOARD.md` were corrected to stop asserting the
  feature is live.
- Taxability tagging (tier 2) is now a feature with no consumer — a user can still mark a
  category tax-free and nothing reads it. Kept deliberately: it is user data, removing it needs
  a migration, and it is precisely what the rebuild will read.

## Alternatives considered

- **Keep hiding it** — rejected: the owner explicitly asked for removal, and a hide that has to
  survive an indefinite "built later" is just dead code with a filter in front of it.
- **Delete tiers 2 and 3 as well** — rejected and confirmed with the owner: it breaks income
  math and vest posting, needs payload migrations, and destroys the exact data a tax engine
  would be built on.
- **Delete `equity_report.py` as originally scoped** — rejected mid-flight (see above): it
  would have taken a working vest report and the `vest_event_id` discovery path with it.

## Agent provenance

- **Decided by:** Claude, on an explicit owner instruction ("let's remove tax supported
  countries and tax concepts here" → "we are not providing any tax features atm, it will be
  built later"), with the tier boundary confirmed by the owner before any deletion.
- **Alternatives weighed:** delete tier 1 only (chosen) vs. keep hiding vs. delete all three
  tiers vs. countries.ts only.
- **Why this way (one line):** the tax *feature* is what is not being provided; the taxability
  tagging and the recorded facts underneath it are user data and load-bearing math that the
  eventual rebuild will read, so the cut follows that seam rather than the word "tax".
