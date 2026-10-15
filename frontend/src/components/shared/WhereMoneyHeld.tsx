import type { ComputedRow } from '../../types/portfolio'
import { fBase, fPct } from '../../utils/format'
import { paletteColor } from '../../utils/constants'
import { EmptyState, Money } from '../ui'

// ── Where money is held ─────────────────────────────────────────────────────
// A net-worth-by-VENUE breakdown across the WHOLE book (cash + receivables + property +
// investments — `usePortfolio` already merges accounts into `rows`). Each venue's total base
// value is a horizontal bar sized by its % of net worth, sorted desc.
//
// Venue key = `category` (an account's name / "Receivables" / a property name). Only venues
// with a POSITIVE net value are shown — a venue that nets negative (e.g. a credit-card-only
// venue) holds debt, not money, so it has no place in a "where money is held" view;
// percentages stay anchored to total net worth.
export function WhereMoneyHeld({ rows, total }: { rows: ComputedRow[]; total: number }) {
  const venues: Record<string, number> = {}
  const venueColors: Record<string, string> = {}
  rows.forEach((r) => {
    // Group by institution so Vantage Brokerage-USD + Vantage Brokerage-SGD merge under "Vantage Brokerage".
    // Fall back to account name (category) then 'Unassigned'. The `??` guard
    // handles rows where institution may be missing on the type (e.g. cash rows).
    const key = ((r.institution ?? '') || r.category || '').trim() || 'Unassigned'
    venues[key] = (venues[key] || 0) + r._base
    if (r.color && !venueColors[key]) venueColors[key] = r.color
  })

  const sorted = Object.keys(venues)
    .filter((v) => venues[v]! > 0)
    .sort((a, b) => venues[b]! - venues[a]!)

  if (!sorted.length || total <= 0) {
    return (
      <EmptyState
        title="No venue breakdown yet."
        body="Add accounts or holdings and they'll roll up here by where they're held."
      />
    )
  }

  const max = Math.max(...sorted.map((v) => venues[v]!))

  return (
    <div className="wmh">
      {sorted.map((v) => {
        const value = venues[v]!
        const pctOfNet = total > 0 ? (value / total) * 100 : 0
        // Bar width is relative to the largest venue so the longest bar fills the track;
        // the label reports % of NET WORTH (the meaningful figure).
        const barPct = max > 0 ? (value / max) * 100 : 0
        const color = venueColors[v] || paletteColor(v)
        return (
          <div className="wmh-row" key={v}>
            <i style={{ background: color }} />
            <span className="wmh-nm" title={v}>
              {v}
            </span>
            <div className="wmh-bar">
              <span className="wmh-bar-fill" style={{ width: `${barPct}%`, background: color }} />
            </div>
            <Money className="wmh-val">{fBase(value)}</Money>
            <span className="wmh-pct">{fPct(pctOfNet)}</span>
          </div>
        )
      })}
    </div>
  )
}
