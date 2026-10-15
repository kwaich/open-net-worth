// Types for the Assets tab — things you own (property, vehicles, watches & jewellery, art,
// precious metals, cash-value policies, user-created categories) valued by dated manual
// valuations, not a ledger balance or a price feed.
//
// Mirrors the wire contract in docs/adr/2026-09-06-assets-tab-faithful-port.md as amended by
// .claude/rfcs/assets-create-edit-redesign.md §3 — field names stay snake_case (the wire
// shape), exactly like types/commitments.ts.

/** Every stored value is a `valuation`. The one `purchase` point is derived on read from
 *  `cost`/`cost_date` — never stored, never the current value, and none for a policy. */
export type AssetMarkKind = 'valuation' | 'purchase'

/** One dated value. `marks[]` on an asset are ordered newest-first. Recording a new value
 *  appends one; correcting a wrong one replaces it in place and stamps `edited_at`; removing
 *  one deletes it. The purchase point can be neither corrected nor removed. */
export interface AssetMark {
  id: string // "mk_<hex>", or the literal "purchase" for the derived purchase point
  kind: AssetMarkKind
  date: string // ISO — the date this value was AS OF, not when it was entered
  value: number
  note: string
  by: 'you' | 'automation'
  created_at: string
  edited_at: string | null // set when a value was corrected in place
}

/** An attachment (receipt, statement, appraisal…) referenced from the asset. Served by the
 *  existing GET /api/uploads/{ref_id}/image|file. */
export interface AssetDoc {
  ref_id: string
  name: string
  added_on: string
  lucius_readable: boolean
  /** The user's note on the file — the same one Documents shows; absent when there is none. */
  note?: string | null
  /** What the file is — its own name, type and size (the doc's `name` may be a rename). */
  filename?: string | null
  media_type?: string | null
  size_bytes?: number | null
}

/** A category — the six built-ins plus any user-created ones (`custom: true`, generic-icon
 *  fallback). `color` is a `var(--cat-*)` token reference, resolved at render time. */
export interface AssetCategory {
  key: string
  label: string
  plural: string
  color: string
  custom: boolean
  hint?: string
}

/** The live asset a loan secures. */
export interface AssetHolder {
  id: string
  name: string
}

/** A liability account an asset points at. The account itself lives in Accounts — this is a
 *  read-only projection (ledger-computed balance, display-only rate/term labels). A loan
 *  secures at most one live asset; `held_by` names it (the oldest, for legacy data that links
 *  two), or is null when the loan is free. */
export interface AssetLoan {
  id: string
  name: string
  balance: number // positive, base currency
  rate_label: string // e.g. "3.1%", or "" when not tracked
  term_label: string // e.g. "to 2044", or "" when not tracked
  held_by: AssetHolder | null
}

/** One owned asset. `value`/`valued_on` are the newest valuation — no identity edit changes
 *  them (a cost/cost_date/category edit only moves the derived purchase point in `marks`).
 *  Read the current value from `value`, never from `marks[0]`. */
export interface OwnedAsset {
  id: string // "oa_<12hex>"
  name: string
  category: string
  description: string
  photo_ref: string | null
  facts: [string, string][]
  notes: string
  cost: number | null // for a policy: premiums paid so far
  cost_date: string | null // ISO date; for a policy: the date it started
  share_pct: number
  share_note: string
  loan_account_id: string | null
  insured: boolean
  maturity_date: string | null
  currency: string
  // null only for an imported asset that has no values yet
  value: number | null
  valued_on: string | null // ISO
  share_value: number | null // value × share_pct / 100, in the asset's own currency
  /** The register's figures in the BASE currency, at the page's rates (GET /api/assets only):
   *  the whole value it orders by, and your share of value and cost a category stack sums. */
  base_value?: number | null
  base_share_value?: number | null
  base_share_cost?: number | null
  marks: AssetMark[] // newest first, the derived purchase point included
  docs: AssetDoc[]
  archived: boolean
  disposed_on: string | null
  disposal_proceeds: number | null
  disposal_txn_id: string | null
  disposal_account_id: string | null
  created_at: string
  updated_at: string
}

/** A cash account option for the dispose flow's "Proceeds into" picker. */
export interface CashAccountOption {
  id: string
  name: string
  balance_base: number
}

/** A class card's optional breakdown row (e.g. Positions vs. Settlement cash) — the server
 *  emits this ONLY when there are two or more kinds to show; the client never derives it. */
export interface AssetSummaryRow {
  key: string
  label: string
  value: number
  pct?: number | null
}

/** The Cash class card — cash is live from bank feeds, so it never carries a gain. */
export interface AssetSummaryCashClass {
  value: number
  sub: string
  yield_label: string | null
  gain: null
  gain_pct: null
  as_of: string
  count_label: string
  rows: AssetSummaryRow[] | null
}

/** The Investments class card — marked by the market, so it does carry a gain. `gain_pct`
 *  is null exactly when `rows` isn't (owned_assets.py's `_investments_card`) — once
 *  settlement cash splits the headline into Positions/Settlement cash rows, the card-level
 *  % can't be blended with a non-percentage-bearing cash figure, so it moves onto the
 *  Positions row's own `pct` instead (see register.tsx's ClassCard `headlinePct`). */
export interface AssetSummaryInvestmentsClass {
  value: number
  sub: string
  gain: number
  gain_pct: number | null
  as_of: string
  count_label: string
  rows: AssetSummaryRow[] | null
}

/** The Retirement class card — its accounts' holdings and the cash beside them. It follows the
 *  Investments card: a `gain` while anything is held, and once it holds both, Positions/Cash
 *  `rows` with the return moved onto Positions (`gain_pct` null). All cash: one figure, no
 *  gain. `null` when the book has no retirement account. */
export interface AssetSummaryRetirementClass {
  value: number
  count_label: string
  gain: number | null
  gain_pct: number | null
  rows: AssetSummaryRow[] | null
}

/** The Receivable class card — one figure, no gain, no rows; `null` when the book has no
 *  receivable account (the card then never renders). */
export interface AssetSummarySimpleClass {
  value: number
  count_label: string
}

/** The Debt class card — Receivables' counterpart. `value` is the "What you owe" KPI as a
 *  positive magnitude; `rows` is the rail's Loans/Credit breakdown (null with one kind).
 *  `null` when the book has no credit or loan account. */
export interface AssetSummaryDebtClass {
  value: number
  count_label: string
  rows: AssetSummaryRow[] | null
}

/** One row of the register's "Asset Composition" rail — a class (cash, investments,
 *  retirement, receivable) or an owned-asset category, share-adjusted and base-converted.
 *  `pct` is a whole percentage; the rows' `pct` values always sum to 100. */
export interface AssetCompositionRow {
  key: string
  kind: 'class' | 'category'
  value: number
  pct: number
}

/** Built on the server from the same net-worth breakdown as `own` — the client looks up each
 *  row's label and colour by `key` and never divides. No rows → `{ total: 0, rows: [] }`. */
export interface AssetComposition {
  total: number
  rows: AssetCompositionRow[]
}

/** Server money math (ledger.net_worth() + portfolio_analytics) — the register's summary
 *  strip, its "peer" class cards (Cash, Investments, Retirement, Receivable) and the
 *  composition rail are never client-derived. */
export interface AssetsSummary {
  own: number
  owe: number
  net_worth: number
  unrealized: number
  classes: {
    cash: AssetSummaryCashClass
    investments: AssetSummaryInvestmentsClass
    retirement: AssetSummaryRetirementClass | null
    receivable: AssetSummarySimpleClass | null
    debt: AssetSummaryDebtClass | null
  }
  /** Liability breakdown (credit vs loans) for the rail's "Debt Composition" card — same
   *  "two or more kinds" rule as a class card's own `rows`: null with a single liability
   *  type, since a lone row would just restate the "What you owe" KPI. */
  debt: AssetSummaryRow[] | null
  composition: AssetComposition
}

/** GET /api/assets response body. */
export interface AssetsPayload {
  assets: OwnedAsset[]
  categories: AssetCategory[]
  loans: AssetLoan[]
  cash_accounts: CashAccountOption[]
  summary: AssetsSummary
  base_currency: string
}
