---
title: Multi-position read surfaces — aggregate across accounts, drop per-holding crypto currency
status: Accepted
date: 2026-07-15
deciders: orchestrating Claude agent + repo owner (explicit task decisions, 2026-07-15)
---

# Multi-position read surfaces (custodian model) + crypto base-currency

## Context

The custodian model (one securities account per broker, holding all positions/lots/grants for
that custodian) makes "one liquid position per instrument×account" a **per-account** invariant,
not a portfolio-wide one. Two read surfaces assumed the latter and were wrong for a real user
holding the same instrument at more than one broker:

1. `backend/services/asset_detail.py::_position_block` took `matching[0]` — the first liquid
   position found — silently dropping every other account's shares from qty/value/P&L. It also
   excluded `bucket="locked"` positions entirely, so a partially-vested RSU grant (e.g. 1,731
   vested + 1,466.25 unvested) showed only the vested slice, with no signal the rest existed.
2. The Holdings tab's asset-class/currency groupings rendered one row PER POSITION, so the same
   instrument held at two accounts appeared as two separate rows even when the grouping itself
   had nothing to do with account (misleading — reads as two different holdings).

Separately, crypto's asset-detail chart/headline followed `?currency=`/`vs=`, populated from
"the cost currency of the holding the user opened this from" — so the SAME coin read as a
different currency (and a genuinely different chart, since CoinGecko serves native per-fiat
pairs) depending on which account's row you clicked. Owner ruled this wrong: crypto must always
show in the portfolio's base currency.

## Decision

**Backend (`_position_block`):** aggregate across every liquid position of the instrument —
summed qty/native value/base value; each position's cost basis converted to base
INDIVIDUALLY (never mixing currencies raw) before summing; unrealized P&L is `null` the instant
ANY contributing position's cost is unknown (never fabricated by silently dropping the unknown
leg). A new `per_account` array carries the untouched per-position detail (account, qty, market
value, native cost + currency, unrealized P&L) regardless. Top-level `position_id` stays a plain
string only when exactly one position contributes; otherwise `null` (ambiguous — callers fall
back to a non-mention-backed rendering). Top-level native `cost_basis`/`cost_currency` (a single
per-unit number in ONE currency) collapse to `null` when contributing positions carry different
cost currencies — same "never mix currencies raw" rule, at the display-number level. Locked
(unvested) positions fold into a separate `unvested` block (`{units, market_value_base, grant_id,
next_vest}`, grant resolved via `GrantStore`) rather than being blended into sellable `qty` or
excluded entirely.

**Frontend (Holdings tab, `useInvestmentsBook.mergeByInstrument`):** rows of the same instrument
merge into ONE row (summed base value/P&L, weight recomputed as the sum of the merged weights,
today% taken from any one row since it's a per-instrument ratio, unchanged by merging) whenever
the active "Group by" does **not** itself distinguish accounts (asset class / currency / none).
Grouping by account is exempt — each group is already scoped to one account, so there is nothing
to merge. **No venue sublabel** (owner explicitly declined it) — a merged row's account field is
a bare count ("2 accounts"), never a list of venue names. A merged row disables the Sell/Dividend
row actions and the mention `EntityRow` wrapper (both need one concrete `position_id`); clicking
it still opens the (now aggregate) asset-detail page, whose Position tab renders the full
`per_account` breakdown.

**Crypto currency:** `_display_currency` drops its `prefer` parameter entirely — crypto always
resolves to the portfolio base currency (falling back to the coin's quote currency only when
CoinGecko doesn't serve that fiat). The entire per-holding currency-preference plumbing (the
`?currency=`/`vs=` query params, `AssetPosition`'s `preferredCurrency` prop chain through
`AssetDetailView`/`useAssetDetail`/the `/asset/$instrumentId` route search param, `AssetNavParams
.currency`, `AssetLink`'s `currency` prop) is removed rather than left inert — it had no other
consumer once crypto stopped honoring it, and equities never used it in the first place.

## Consequences

### Positive

- Net worth / weight / unrealized P&L for a multi-custodian holding are now correct instead of
  silently undercounting every account past the first.
- A partially-vested equity grant is fully visible (vested + unvested, with a grant deep-link)
  instead of the app implying the unvested units don't exist.
- The Holdings tab no longer double-lists a cross-account holding under non-account groupings.
- Two holders of the same coin (or the same holder via two accounts) always see an identical
  chart/headline — no more "which account did I open this from" nondeterminism, and a stale
  bookmark's `?currency=` can no longer resurrect the old behavior (removed server-side, not
  just unwired on the client).
- Smaller surface: an entire cross-cutting currency-preference plumbing path is gone.

### Negative

- A multi-account holding's Position tab no longer shows a single combined lots/realized table
  (LotsPanel is single-position-scoped) — it shows a note pointing at the per-account summary
  instead. Combining lots across accounts is deferred (see Follow-ups).
- A merged Holdings row can't Sell/Dividend directly; the user must open the asset detail and
  act from there (out of scope for this change — no per-account action picker was built).
- `AssetPosition.position_id` and top-level `cost_basis` are now nullable — every frontend
  consumer had to be audited for the assumption they're always present (`PositionSubtab`,
  `YourPositionCard`, `AssetDetailView`'s right-rail `EntityRow`).

### Follow-ups

- A combined (or per-account-selectable) lots/realized view for a merged holding's Position tab.
- A per-account action picker so a merged Holdings row can still Sell/record a dividend without
  first opening the asset-detail page.
- Multiple grants backing the same instrument's locked position(s) resolve to only the first
  matching grant (the current equity-comp model assumes one locked position per grant; revisit
  if that assumption changes).

## Alternatives considered

- **Keep `matching[0]` but sort by value first** — rejected: still silently drops every other
  account, just picks a different wrong one.
- **Show per-account rows on the Holdings tab always (no merge)** — rejected by the owner's
  explicit "merge under non-account groupings" instruction; a cross-account holding under
  Asset-Class grouping reading as two unrelated rows is the bug being fixed.
- **Add a venue sublabel to a merged row** — rejected by the owner ("NO venue sublabel").
- **Keep the per-holding crypto currency preference but default it to base** — rejected: the
  mechanism becomes fully inert (no other consumer), so keeping the plumbing around is dead
  weight; removing it also closes the stale-bookmark loophole a client-only fix would leave open.

## Agent provenance

- **Decided by:** orchestrating Claude agent, following explicit owner-specified requirements
  for this lane (custodian-model aggregation, no venue sublabel, crypto always base currency)
- **Lane / branch:** `worktree-agent-a3650cbef6e68cbc5`
- **Alternatives weighed:** see above
- **Driving artifacts:** CLAUDE.md's custodian-model paragraph (`accounts.json` data model
  section); the ONWAI vested/unvested case cited in the task (1,731 liquid + 1,466.25 locked)
- **Why this way (one line):** the data model already supports multi-account/locked positions —
  the read layer was the bug; fix it to match the model instead of adding a workaround on top.
