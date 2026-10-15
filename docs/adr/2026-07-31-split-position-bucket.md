# 2026-07-31 — Split the overloaded `bucket` into `vesting_state` + derived `liquidity`

**Status:** Accepted
**Owner:** the maintainer (raised the overload and its cost in-session)
**Touches:** `backend/models/position.py`, `backend/db/position_store.py`,
`backend/services/positions.py`, `equity.py`, `equity_release.py`, `ledger.py`,
`portfolio_history.py`, `asset_detail.py`, `fixed_income.py`, `macro_exposure.py`,
`backend/ai/prompts.py`, `backend/scripts/migrate_position_bucket_to_vesting_state.py`,
`frontend/src/types/portfolio.ts`, `hooks/usePortfolio.ts`,
`components/tabs/overview/widgets/netWorth.tsx`, `HoldingsTable.tsx`,
`MovePositionModal.tsx`, `useInvestmentsBook.ts`

## Context

`Bucket = liquid | contingent | locked` meant **two different things** depending on which
side of the wire you were on.

**Backend — entitlement.** `services/equity.py` mints a `bucket="locked"` position holding a
grant's UNVESTED units. `locked` there means *"not the holder's yet — forfeited if the
grantee leaves."*

**Frontend — reachability.** `toAccountRow()` in `usePortfolio.ts` synthesised account rows
and assigned property → `locked`, loan receivables → `contingent`. `locked` there means
*"illiquid."* The Overview "Reach" widget rendered both senses in one bar, with a label that
gave the game away: *"Contingent — unvested equity **and** money owed to you."*

The cost was not cosmetic. When net worth was corrected to exclude unvested equity
(ADR `2026-07-31-unvested-equity-excluded-from-net-worth`), the obvious filter —
`bucket === 'locked'` — would have **deleted the owner's ~S$300K property from net worth**.
Shipping it at all required adding a `_unvested` boolean tagged at row-assembly time, where
provenance was still known, purely to tell the two senses apart. That tag was a workaround
for the overload, and it should not have had to exist.

## Decision

Two fields, because there are two questions.

| | Field | Values | Stored? |
|---|---|---|---|
| **Is it the holder's?** | `vesting_state` | `vested` \| `unvested` | **stored** on the position |
| **How reachable is it?** | `liquidity` | `liquid` \| `semi_liquid` \| `illiquid` | **derived**, never stored |

Net worth filters on `vesting_state` and nothing else. Property is never in scope for it.

**Naming was researched, not invented.** The first draft used `ownership: owned | unvested`,
which the owner correctly rejected as non-parallel — one term is a general ownership state,
the other equity-comp jargon. Checking the systems of record:

- **Plaid** models this as `vested_quantity` / `vested_value` on a holding, and has no
  "ownership" concept at all.
- **Carta**'s RSU ladder is Issued → **Vested** → Released for settlement → Delivered.
- **IFRS 2** defines vested as *"no longer contingent on fulfilling any vesting conditions."*

So `vested`/`unvested` it is — parallel, and the vocabulary the domain already uses. It also
stops the field over-generalising: in this app the ONLY writer of a non-vested row is the
equity choke; cash, property and receivables are trivially entitled and default to `vested`.

The liquidity tiers are likewise the standard personal-finance split (SmartAsset, NerdWallet,
First Citizens, ProjectionLab) — liquid / semi-liquid / illiquid — rather than a `pending`
tier we would have made up. Vested-but-undelivered shares land in `semi_liquid` naturally.

**`contingent` is deliberately NOT reused for the entitlement axis**, despite IFRS 2's wording
inviting it. Legacy `bucket="contingent"` meant a *fully entitled* holding; a new `contingent`
would mean *not entitled* — the exact opposite. A migration slip there would silently misstate
net worth, which is the class of bug this ADR removes.

## Consequences

- **`_unvested` is deleted.** `usePortfolio` reads `vesting_state` straight off the server;
  an account row is `vested` by construction. Its regression tests were **updated, not
  deleted** — the behaviours they assert still hold, now stated against the real field.
- **The Reach widget picks one concept and says so.** It reports reachability only
  ("Liquid now" / "Delayed" / "Illiquid"), and skips unvested rows entirely, since those are
  absent from net worth and have no share of it to report.
- **Two latent breakages the split exposed, both fixed:** a grant's pending-release position
  is now `vested` like its delivered sibling, so (a) `find_or_create_liquid_position` had to
  stop scanning by value and exclude the grant's own rows by id — otherwise a released lot
  would be booked onto the still-undelivered units — and (b) the duplicate-position guard
  had to skip grant-managed rows, which it now does via the same `_grant_managed_ok` seam
  `update_position` already used.
- **Migration:** `backend/scripts/migrate_position_bucket_to_vesting_state.py` (dry-run by
  default, timestamped backup, atomic write). Dry-run against a copy of a representative
  book: 43 → `vested`, 1 → `unvested` (the ONWAI grant's locked position), **zero
  `contingent` rows** — confirming property was never a stored position at all, only a
  frontend-synthesised row, which is exactly why the collision lived in `toAccountRow`.
  The runtime does not depend on the script: `position_store.vesting_state_of` applies the
  same mapping on read, so an un-migrated file behaves correctly.
- `Bucket` survives in `models/position.py` marked DEPRECATED, so the migration and the
  legacy read path can still name the old values. Nothing branches on it.
- **Pinned both directions** in `usePortfolio.propertyVsUnvested.test.ts`: illiquid property
  stays IN net worth, unvested equity stays OUT, and a test asserts the two rows are
  distinguishable — same `liquidity`, opposite `vesting_state` — so a future collapse back
  into one field fails loudly.

## Agent provenance

Implemented by Claude Opus 5 on 2026-07-31, from the owner's diagnosis of the overload and
its `_unvested` workaround. The owner rejected the first naming attempt and required the
terminology be researched against real systems rather than invented; that research changed
both axes' names and is recorded above. The owner chose the one-time migration script over
derive-on-read.
