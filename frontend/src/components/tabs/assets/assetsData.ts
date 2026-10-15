// Assets tab engine — a typed port of the design handoff's data.jsx
// (design_handoffs/assets_handoff/prototype/assets_tab/data.jsx), minus the mock
// ASSETS/LOANS/CLASSES/OTHER_DEBT constants (real data now comes from GET /api/assets via
// useAssets.ts) and minus Icons (moved to icons.tsx). Per-asset display arithmetic (equity,
// gain, gain %, years held) stays client-side — pure arithmetic over server numbers (ADR
// Decision 7); the summary strip, class cards and composition rail are server money math
// (classCardsFrom() and compositionView() below only reshape what the API computed).
import type {
  AssetCategory,
  AssetComposition,
  AssetHolder,
  AssetLoan,
  AssetMark,
  AssetSummaryRow,
  AssetsSummary,
  OwnedAsset,
} from '../../../types/assets'
import { fBaseCompact, fBaseWhole, fDate, fMonth } from '../../../utils/format'

/** Fallback categories so the UI never renders without any — same shape as the prototype's
 *  `CATS` (minus its two mock custom entries, which only existed to seed the demo data).
 *  The real category list comes from GET /api/assets's `categories[]`; this is what a
 *  brand-new instance (no user-created categories yet) sees before that round trip lands, and
 *  what a render falls back to if the server ever returns an empty list. */
export const BUILTIN_CATS: AssetCategory[] = [
  {
    key: 'property',
    label: 'Property',
    plural: 'Property',
    color: 'var(--cat-property)',
    custom: false,
  },
  {
    key: 'vehicle',
    label: 'Vehicle',
    plural: 'Vehicles',
    color: 'var(--cat-vehicle)',
    custom: false,
  },
  {
    key: 'watch',
    label: 'Watch & jewellery',
    plural: 'Watches & jewellery',
    color: 'var(--cat-watch)',
    custom: false,
  },
  {
    key: 'art',
    label: 'Art & collectibles',
    plural: 'Art & collectibles',
    color: 'var(--cat-art)',
    custom: false,
  },
  {
    key: 'metal',
    label: 'Precious metals',
    plural: 'Precious metals',
    color: 'var(--cat-metal)',
    custom: false,
    hint: 'Physical bullion and coins you hold. Gold ETFs like GLD are market positions and already live under Investments.',
  },
  {
    key: 'policy',
    label: 'Insurance & endowments',
    plural: 'Insurance & endowments',
    color: 'var(--cat-policy)',
    custom: false,
    hint: 'Only policies with a cash value — whole life, endowments, ILPs — valued at surrender value from the annual statement. Term, health and car cover have no value and belong in Plan.',
  },
  {
    key: 'other',
    label: 'Other',
    plural: 'Other',
    color: 'var(--cat-other)',
    custom: false,
  },
]

/** Resolves a category key to its definition, falling back to the last entry ("Other") —
 *  mirrors the prototype's `catOf`, generalized to take the real (server + built-in)
 *  category list instead of reading a module-level mock array. */
export function catOf(cats: AssetCategory[], key: string): AssetCategory {
  const list = cats.length ? cats : BUILTIN_CATS
  return list.find((c) => c.key === key) ?? list[list.length - 1]!
}

/** Net-worth band label, verbatim — precious metals band as a commodity, everything else
 *  (property, vehicles, collectibles, policies…) as locked/illiquid property. */
export function bandOf(a: OwnedAsset): string {
  return a.category === 'metal' ? 'Commodity · locked' : 'Property · locked'
}

/** A share as a person says it — "50", "33.3", never "33.333333". */
export function sharePct(pct: number): string {
  return String(Math.round(pct * 10) / 10)
}

/** "100%" or "60% — joint with A. Lim" — verbatim. */
export function shareOf(a: OwnedAsset): string {
  const pct = a.share_pct == null ? 100 : a.share_pct
  return `${sharePct(pct)}%${a.share_note ? ' — ' + a.share_note : ''}`
}

/** The liability account this asset points at, if any. */
export function loanOf(asset: OwnedAsset, loans: AssetLoan[]): AssetLoan | null {
  if (!asset.loan_account_id) return null
  return loans.find((l) => l.id === asset.loan_account_id) ?? null
}

export function debtOf(asset: OwnedAsset, loans: AssetLoan[]): number {
  const l = loanOf(asset, loans)
  return l ? l.balance : 0
}

/** Whole-asset value minus the linked loan — null for an asset with no value yet. */
export function equityOf(asset: OwnedAsset, loans: AssetLoan[]): number | null {
  return asset.value == null ? null : asset.value - debtOf(asset, loans)
}

export function gainOf(asset: OwnedAsset): number | null {
  return asset.cost == null || asset.value == null ? null : asset.value - asset.cost
}

export function gainPctOf(asset: OwnedAsset): number | null {
  if (!asset.cost || asset.value == null) return null
  return ((asset.value - asset.cost) / asset.cost) * 100
}

const MS_PER_YEAR = 365.25 * 24 * 60 * 60 * 1000

/** Years from the purchase date to the current value's date. Null when either date is
 *  missing or the span isn't positive — never the span of the value history, which starts
 *  wherever the first recorded value happens to fall. */
export function yearsHeldOf(asset: OwnedAsset): number | null {
  if (!asset.cost_date || !asset.valued_on) return null
  const years =
    (new Date(asset.valued_on).getTime() - new Date(asset.cost_date).getTime()) / MS_PER_YEAR
  return years > 0 ? years : null
}

/** Compound yearly return on the purchase price, in percent. Null for a policy (its "cost" is
 *  premiums paid over time, not one price on one date), and whenever there is no price, no
 *  value or no span. */
export function annualizedReturnOf(asset: OwnedAsset): number | null {
  if (asset.category === 'policy' || !asset.cost || asset.value == null) return null
  const years = yearsHeldOf(asset)
  return years == null ? null : (Math.pow(asset.value / asset.cost, 1 / years) - 1) * 100
}

/** Whether a loan can be picked for an asset: free, already this asset's own loan, or held
 *  by another live asset (named, so the picker can say which). `assetId` is null while
 *  creating. */
export type LoanOptionState = 'free' | 'mine' | { heldBy: AssetHolder }

export function loanOptionState(loan: AssetLoan, assetId: string | null): LoanOptionState {
  if (!loan.held_by) return 'free'
  if (assetId !== null && loan.held_by.id === assetId) return 'mine'
  return { heldBy: loan.held_by }
}

/** Base-currency formatter — the app's whole-figure `fBaseWhole` (profile.base_currency):
 *  the prototype never shows cents on a value, and `.00` on a headline figure is noise. */
export const fmt = fBaseWhole
/** Compact base-currency formatter for tight spaces (rail composition, table deltas). */
export const fmtK = fBaseCompact

/** `fmt`, or "—" for a figure an imported asset doesn't have yet. */
export function fmtOrDash(n: number | null): string {
  return n == null ? '—' : fmt(n)
}

/** "+13.3%" / "−4.0%" / "+0.0%" — verbatim, true minus sign. */
export function pct(n: number): string {
  const sign = n > 0 ? '+' : n < 0 ? '−' : ''
  return sign + Math.abs(n).toFixed(1) + '%'
}

/** ISO date → "Apr 2026", or "—" when there is no date. */
export function monthYear(iso: string | null): string {
  return iso ? fMonth(iso, 'short', 'always') : '—'
}

/** ISO date → "26 Sep 2026", or "—" when there is none. Only the calendar day is read: a
 *  `Date` built from "2026-09-26" is UTC midnight, the day before anywhere west of Greenwich. */
export function fullDate(iso: string | null): string {
  return iso && /^\d{4}-\d{2}-\d{2}/.test(iso) ? fDate(iso.slice(0, 10), 'short', 'always') : '—'
}

/** The values the user recorded, newest first — every point but the derived purchase one. */
export function regularMarks(asset: OwnedAsset): AssetMark[] {
  return asset.marks.filter((m) => m.kind === 'valuation')
}

/** The purchase point, derived from cost/cost_date — null when there is none (a policy never
 *  has one). It follows Purchase: it can be neither corrected nor removed. */
export function purchaseMark(asset: OwnedAsset): AssetMark | null {
  return asset.marks.find((m) => m.kind === 'purchase') ?? null
}

/** The date of the oldest recorded value — the purchase can't be dated after it. */
export function firstValuedOn(asset: OwnedAsset): string | null {
  return regularMarks(asset).reduce<string | null>(
    (first, m) => (first == null || m.date < first ? m.date : first),
    null,
  )
}

/** A recorded value can be removed unless it is the asset's last one. */
export function canRemoveMark(asset: OwnedAsset, mark: AssetMark): boolean {
  return mark.kind === 'valuation' && regularMarks(asset).length > 1
}

/** The "peer" class cards (Cash, Investments, Retirement, Receivable, Debt) — same chrome, same sort
 *  slot as an asset card, but they open their own tab instead of a detail view (ADR Decision
 *  7). Reshapes the server-computed `summary.classes` block; never re-derives the money itself.
 *  `rows` renders only when the server sent two or more (the "two-or-more kinds" rule lives
 *  entirely server-side — see owned_assets.py::_cash_card/_investments_card). */
export interface ClassCardModel {
  key: 'cash' | 'investments' | 'retirement' | 'receivable' | 'debt'
  label: string
  plural: string
  color: string
  tab: string
  name: string
  sub: string
  value: number
  gain: number | null
  gainPct: number | null
  yield?: string | null
  rows: AssetSummaryRow[] | null
}

// Tokenised like every other category accent (`--cat-*`, design/tokens.source.mjs), so a raw
// hex never sits in a component and the swatch follows the theme.
const RETIREMENT_COLOR = 'var(--cat-retirement)'
const RECEIVABLE_COLOR = 'var(--cat-receivable)'
const DEBT_COLOR = 'var(--bad)'

export function classCardsFrom(summary: AssetsSummary): ClassCardModel[] {
  const { cash, investments, retirement, receivable, debt } = summary.classes
  const cards: ClassCardModel[] = [
    {
      key: 'cash',
      label: 'Cash',
      plural: 'Cash',
      color: 'var(--cat-cash)',
      tab: 'accounts',
      name: 'Cash & Savings',
      sub: 'Bank & e-wallet accounts',
      value: cash.value,
      gain: null,
      gainPct: null,
      yield: cash.yield_label,
      rows: cash.rows,
    },
    {
      key: 'investments',
      label: 'Investments',
      plural: 'Investments',
      color: 'var(--cat-invest)',
      tab: 'investments',
      name: 'Investments',
      sub: 'Non-retirement brokerage & crypto',
      value: investments.value,
      gain: investments.gain,
      gainPct: investments.gain_pct,
      rows: investments.rows,
    },
  ]
  if (retirement) {
    cards.push({
      key: 'retirement',
      label: 'Retirement',
      plural: 'Retirement',
      color: RETIREMENT_COLOR,
      tab: 'accounts',
      name: 'Retirement',
      sub: 'Retirement accounts',
      value: retirement.value,
      gain: retirement.gain ?? null,
      gainPct: retirement.gain_pct ?? null,
      rows: retirement.rows ?? null,
    })
  }
  if (receivable) {
    cards.push({
      key: 'receivable',
      label: 'Receivables',
      plural: 'Receivables',
      color: RECEIVABLE_COLOR,
      tab: 'accounts',
      name: 'Receivables',
      sub: 'Money owed to you',
      value: receivable.value,
      gain: null,
      gainPct: null,
      rows: null,
    })
  }
  // What you owe, signed as it reads everywhere else ("What you owe −S$…"): the server sends
  // magnitudes, so the card and its rows negate them for display only.
  if (debt) {
    cards.push({
      key: 'debt',
      label: 'Debt',
      plural: 'Debt',
      color: DEBT_COLOR,
      tab: 'accounts',
      name: 'Debt',
      sub: 'Money you owe',
      value: -debt.value,
      gain: null,
      gainPct: null,
      rows: debt.rows?.map((r) => ({ ...r, value: -r.value })) ?? null,
    })
  }
  return cards
}

/** One composition-rail row, ready to draw. */
export interface CompositionRowView {
  key: string
  label: string
  color: string
  value: number
  pct: number
}

/** Labels and colours the server's composition rows — a class from its peer class card, a
 *  category from the category list (its full `plural` name, like the filter chip beside it).
 *  No arithmetic: `pct` is the server's own whole percentage, and the rows keep the server's
 *  order. A key with no definition falls back to the raw key and a neutral colour. */
export function compositionView(
  composition: AssetComposition,
  cats: AssetCategory[],
  classes: ClassCardModel[],
): CompositionRowView[] {
  return composition.rows.map((r) => {
    const cls = r.kind === 'class' ? classes.find((c) => c.key === r.key) : undefined
    const cat = r.kind === 'category' ? cats.find((c) => c.key === r.key) : undefined
    return {
      key: r.key,
      label: cls?.label ?? cat?.plural ?? r.key,
      color: cls?.color ?? cat?.color ?? 'var(--faint)',
      value: r.value,
      pct: r.pct,
    }
  })
}
