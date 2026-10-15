# Bonds / Fixed-Income — UI build brief

Design authority for the bonds-first-class frontend. Derived from the `ui-designer` spec;
the backend contract below is what actually shipped (source of truth — use these field names).

## Placement (decided)

NO new tab and NO new `InvTab`. The Investments → Holdings sub-tab already derives asset-class
sections from the `asset_class` values actually held (`Investments.tsx` ~line 54). Wiring `bond`
into the constants makes a "Fixed income" section appear automatically. The change is:

1. `bond` → `ASSET_CLASS_COLOR` / `ASSET_CLASS_LABEL` / `ASSET_CLASS_ORDER` in `constants.ts`.
2. Bond-specific columns in the Holdings table for `asset_class === 'bond'` rows.
3. A new `BondMaturityCard` shared component below the Bonds section.
4. A "By income type" block in `IncomePanel` (reads existing `summary.by_kind`).
5. `RecordDividendForm` defaults its Type to **Interest** for bond rows.

## Backend contract (already implemented — do not change backend)

**Position rows** (from `GET /api/portfolio` / the portfolio hook) now carry these flat fields
when `asset_class === 'bond'` (absent otherwise):

| field | type | meaning |
|---|---|---|
| `bond_type` | `'' \| market \| ssb \| tbill \| etf \| fd` | subtype / badge |
| `face_value` | number | par per unit |
| `coupon_rate` | number \| null | annual rate as a **fraction** (0.035 = 3.5%) |
| `coupon_freq` | number | coupons per year (**0 = zero-coupon**, e.g. a T-bill) |
| `maturity_date` | string \| null | ISO date |
| `issue_date` | string \| null | ISO date |
| `days_to_maturity` | number \| null | computed days to maturity |

**`GET /api/fixed-income`** → use this for the `BondMaturityCard` (base-currency, server-computed):
```
{
  base_currency: string,
  total_base: number,
  count: number,
  by_subtype: Record<string, number>,           // e.g. { ssb: 20000, tbill: 4980, ... }
  maturity_buckets: { d30: number, d90: number, y1: number },   // cumulative, base ccy
  maturity_ladder: [{ instrument_id, name, subtype, maturity_date, proceeds_base }],  // ≤365d, sorted
  holdings: [{ instrument_id, symbol, name, account_name, subtype, coupon_rate,
               maturity_date, days_to_maturity, value_base,
               next_coupon: { date, amount_per_unit } | null }]
}
```
The net-worth chart bond band already comes from the backend (`ledger._MARKET_BAND` bond→bond).
The allocation donut groups by `asset_class`, so it separates bonds once the constants land.

## Constants (`frontend/src/utils/constants.ts`)

- `ASSET_CLASS_COLOR['bond'] = '#34b08a'` (teal-green; distinct from equity `#3b86dd`, commodity, crypto, property).
- `ASSET_CLASS_LABEL['bond'] = 'Fixed income'`.
- `ASSET_CLASS_ORDER`: insert `'bond'` at index 1 (after `equity`, before `crypto`).

## Files to touch (frontend only)

- `frontend/src/utils/constants.ts` — the three maps above.
- `frontend/src/types/portfolio.ts` — extend `Row` (and the computed row type) with the 7 bond fields above.
- `frontend/src/tabs.ts` — `TAB_SUBTITLES['investments']` → mention "and fixed income".
- `frontend/src/components/shared/HoldingsTable.tsx` — add a `bondColumns?: boolean` prop; when true, replace Qty/Cost/Price/Cur with Type(badge) / Coupon / Matures / Face. Coupon: `coupon_freq === 0` → `<Badge tone="muted">Zero-coupon</Badge>`, else `fPct(coupon_rate*100)`. Matures: formatted `maturity_date` + a muted `(<days>d)` via new `.bond-matures-in`; `—` if null. Type badge: market=Market(neutral), ssb=SSB(pos), tbill=T-bill(neutral), etf=ETF(muted), fd=FD(neutral).
- `frontend/src/components/shared/BondMaturityCard.tsx` — NEW. `CollapsibleSection` ("Upcoming maturities & coupons", open on desktop, `defaultOpen={false}` <640px) → `SummaryBar` (Matures ≤30d / ≤90d / ≤1y from `maturity_buckets`, `—` when 0) → a ladder list (mirror `.income-row` grid). Loading/error/empty states required (empty: "No maturities in the next 12 months." body: "Bonds and fixed deposits without a maturity date (e.g. bond ETFs) are not shown here."). Source data from `GET /api/fixed-income` via a small `useFixedIncome` hook (mirror an existing fetch hook).
- `frontend/src/components/tabs/Investments.tsx` — pass `bondColumns` to the bond section's `HoldingsTable`; render `<BondMaturityCard/>` below the bond section (only when bonds are held); pass `defaultKind="interest"` to `RecordDividendForm` for bond rows.
- `frontend/src/components/shared/IncomePanel.tsx` — add a "By income type" block from `summary.by_kind` (labels: dividend→Dividends, interest→Interest, distribution→Distributions). Omit the block when `< 2` kinds present. Reuse `.income-table`/`.income-row` (no new classes).
- `frontend/src/components/shared/RecordDividendForm.tsx` — accept optional `defaultKind` prop, default the kind select to it.
- `frontend/src/index.css` — define EVERY new class in the same change (lint:classes enforces): `.bond-matures-in`, `.bond-maturity-card`, `.bond-ladder-table`, `.bond-ladder-row`, `.bond-ladder-row-head`, `.bond-ladder-date`, `.bond-ladder-name`, `.bond-ladder-type`, `.bond-ladder-proceeds`. Mirror the `.income-*` token usage (`var(--text-xs)`, `var(--muted)`, `var(--border)`, `var(--space-*)`) — no raw px/hex.

## Do NOT

- Add a new tab / InvTab / bond-type filter.
- Touch any backend file (the band dicts, models, stores are done).
- Use `paletteColor('bond')` — use the explicit `ASSET_CLASS_COLOR['bond']`.
- Introduce a className without its `index.css` definition.

## Verify

`cd frontend && npm run build` (includes `lint:classes`) must pass. Add a vitest for the bond
column rendering + coupon/maturity formatting + the by-kind block. Acceptance IDs A–K live in
`docs/qa/acceptance/bonds-first-class.md`.
