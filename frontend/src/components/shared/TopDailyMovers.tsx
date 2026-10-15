import type { KeyboardEvent } from 'react'
import type { ComputedRow } from '../../types/portfolio'
import { EntityRow } from '../../lucius/EntityRow'
import { fBase, fPct, fPrice, plClass, priceDigits } from '../../utils/format'
import { Panel, Text } from '../ui'

const MAX_EACH = 5

interface Props {
  rows: ComputedRow[]
  /** Tapping a mover opens that asset's intel detail page. */
  onOpenAsset?: (row: ComputedRow) => void
  /** Row cap across gainers+losers COMBINED — the Overview widget's 3/5/10/
   * All row-limit convention (`null` = All, no cap). Omitted (every existing
   * caller) keeps the legacy behavior: up to `MAX_EACH` per side, no
   * truncation notice. Passing a number switches to a single combined-
   * ordered list (gainers desc, then losers asc — matching the design
   * handoff) sliced to `limit`, with a "+N more" notice for the rest. */
  limit?: number | null
  /** Suppress the internal Panel/title — for embedding inside a shell (e.g.
   * Overview's WidgetShell) that already renders the widget's own title. */
  hideHeader?: boolean
  /** 'row' renders the design-handoff's 5-column row (a dedicated always-shown
   * ticker-badge column, separate $ and % columns) instead of this component's
   * own 3-column layout — opt-in and additive so every existing caller (e.g.
   * `Gallery.tsx`) is byte-for-byte unaffected. Classes/CSS for 'row' live in
   * the Overview tab's own stylesheet, not here, since it's the only caller. */
  variant?: 'default' | 'row'
  /** `rows` are ALREADY collapsed per instrument and ordered (gainers desc, then losers asc)
   *  by the server, and `maxAbs` is the server's bar-scaling maximum. Set by the Overview,
   *  where collapsing positions into one mover is a sum of money and therefore belongs on the
   *  backend (ADR 2026-07-31-overview-aggregates-are-server-computed). Left unset by callers
   *  that still hand over raw rows (the design Gallery), which keeps the legacy client-side
   *  collapse for them. */
  preAggregated?: boolean
  maxAbs?: number
}

// An instrument held across several accounts/wallets is several positions, each with an
// identical day-over-day % but a different $ move (qty differs). Collapse them to ONE mover
// per instrument so the list reads per-asset, not per-wallet — matching the Intel Brief bar.
// The representative is the largest single contributor (for stable display fields); its
// _daily_pl is replaced by the sum across the group, so the bar/amount show total exposure.
function aggregateByInstrument(rows: ComputedRow[]): ComputedRow[] {
  const groups = new Map<string, ComputedRow[]>()
  for (const r of rows) {
    const key = r.instrument_id || r.symbol || r.ticker || r.position_id || ''
    const g = groups.get(key)
    if (g) g.push(r)
    else groups.set(key, [r])
  }
  return [...groups.values()].map((g) => {
    if (g.length === 1) return g[0]!
    const rep = g.reduce((a, b) =>
      Math.abs(b._daily_pl ?? 0) > Math.abs(a._daily_pl ?? 0) ? b : a,
    )
    const total = g.reduce((s, r) => s + (r._daily_pl ?? 0), 0)
    return { ...rep, _daily_pl: total }
  })
}

export function TopDailyMovers({
  rows,
  onOpenAsset,
  limit,
  hideHeader,
  variant = 'default',
  preAggregated,
  maxAbs: maxAbsProp,
}: Props) {
  const dayRows = preAggregated
    ? rows
    : aggregateByInstrument(rows.filter((r) => r._daily_pl != null))
  if (dayRows.length === 0) return null

  const gainersAll = dayRows
    .filter((r) => (r._daily_pl ?? 0) > 0)
    .sort((a, b) => (b._daily_pl ?? 0) - (a._daily_pl ?? 0))
  const losersAll = dayRows
    .filter((r) => (r._daily_pl ?? 0) < 0)
    .sort((a, b) => (a._daily_pl ?? 0) - (b._daily_pl ?? 0))

  let gainers: ComputedRow[]
  let losers: ComputedRow[]
  let extra = 0
  if (limit === undefined) {
    gainers = gainersAll.slice(0, MAX_EACH)
    losers = losersAll.slice(0, MAX_EACH)
  } else {
    const ordered = [...gainersAll, ...losersAll]
    const shown = limit == null ? ordered : ordered.slice(0, limit)
    extra = ordered.length - shown.length
    gainers = shown.filter((r) => (r._daily_pl ?? 0) > 0)
    losers = shown.filter((r) => (r._daily_pl ?? 0) < 0)
  }

  if (gainers.length === 0 && losers.length === 0) return null

  const maxAbs =
    maxAbsProp ??
    Math.max(
      ...gainers.map((r) => Math.abs(r._daily_pl ?? 0)),
      ...losers.map((r) => Math.abs(r._daily_pl ?? 0)),
    )

  const body = (
    <>
      <div className={variant === 'row' ? 'ov-mv' : 'top-movers'}>
        {gainers.map((r, i) => (
          <MoverRow
            key={r.position_id ?? i}
            row={r}
            maxAbs={maxAbs}
            onOpenAsset={onOpenAsset}
            variant={variant}
          />
        ))}
        {gainers.length > 0 && losers.length > 0 && (
          <div className={variant === 'row' ? 'ov-mv-div' : 'top-movers-divider'} />
        )}
        {losers.map((r, i) => (
          <MoverRow
            key={r.position_id ?? `l${i}`}
            row={r}
            maxAbs={maxAbs}
            onOpenAsset={onOpenAsset}
            variant={variant}
          />
        ))}
      </div>
      {extra > 0 && (
        <div className="top-movers-more">
          +{extra} more position{extra === 1 ? '' : 's'} moved today
        </div>
      )}
      <div className="panel-caption">
        <Text as="p" size="xs" tone="faint">
          Day-over-day change for market-tracked positions. Flat or unpriced holdings are excluded.
        </Text>
      </div>
    </>
  )

  if (hideHeader) return body
  return <Panel title="Today's movers">{body}</Panel>
}

function MoverRow({
  row,
  maxAbs,
  onOpenAsset,
  variant,
}: {
  row: ComputedRow
  maxAbs: number
  onOpenAsset?: (row: ComputedRow) => void
  variant: 'default' | 'row'
}) {
  const pl = row._daily_pl ?? 0
  const pct = row._daily_plpct
  const barPct = maxAbs > 0 ? (Math.abs(pl) / maxAbs) * 100 : 0
  const tone = plClass(pl)
  const ticker = row.symbol || row.ticker
  const name = row.label || ticker || '—'
  const showTicker = ticker && ticker !== name
  // Only rows backed by a tradeable instrument open a detail page.
  const open = onOpenAsset && row.instrument_id && row.ticker ? () => onOpenAsset(row) : undefined

  const posId = row.position_id ?? ''
  // Per-unit price in its display currency, computed server-side (`display_unit_price`) —
  // it scales the native quote by `quote_per_unit` so a per-gram holding reads per gram, not
  // per troy ounce. The `* (quote_per_unit ?? 1)` fallback covers a hand-built row (the design
  // Gallery), which never comes from the server.
  const unitPrice = row._disp_unit_price || +row.price * (row.quote_per_unit ?? 1)
  const unitCcy = row._disp_ccy || row.currency
  const priceStr =
    row.price && +row.price > 0
      ? `${fPrice(unitPrice, priceDigits(row.asset_class))} ${unitCcy}`
      : undefined

  const commonProps = {
    type: 'holding' as const,
    id: posId,
    label: name,
    item: row,
    toFields: () => ({
      name,
      symbol: ticker && ticker !== name ? ticker : undefined,
      day_change_base: (pl >= 0 ? '+' : '') + fBase(pl),
      value_base: fBase(row._base),
      price: priceStr,
      currency: row.currency,
    }),
    search: ticker ?? undefined,
    sublabel: pct != null ? fPct(pct, { sign: true }) : undefined,
    onClick: open,
    onKeyDown: open
      ? (e: KeyboardEvent) => (e.key === 'Enter' || e.key === ' ') && open()
      : undefined,
    role: open ? ('button' as const) : undefined,
    tabIndex: open ? 0 : undefined,
  }

  if (variant === 'row') {
    // The design handoff's `.ov-mv-row` — a fixed 46px ticker-badge column
    // (always shown, even when the ticker matches the name), separate $ and
    // % columns. Faithful port, not the default layout's inline/combined one.
    return (
      <EntityRow {...commonProps} className="ov-mv-row">
        <span className="ov-sym">{ticker || '—'}</span>
        <span className="ov-mv-label">
          <span className="ov-mv-nm">{name}</span>
          {row.price && +row.price > 0 && (
            <span className="ov-mv-price">
              {fPrice(unitPrice, priceDigits(row.asset_class))}
              <span className="ov-mv-ccy">{unitCcy}</span>
            </span>
          )}
        </span>
        <span className="ov-mv-bar">
          <span
            className={`ov-mv-fill ov-mv-fill--${tone}`}
            style={{ width: `${Math.max(1.5, barPct)}%` }}
          />
        </span>
        <span className={`ov-mv-amt money ${tone}`}>
          {pl >= 0 ? '+' : ''}
          {fBase(pl)}
        </span>
        <span className="ov-mv-pct">{fPct(pct, { sign: true })}</span>
      </EntityRow>
    )
  }

  return (
    <EntityRow
      {...commonProps}
      className={`top-movers-row${open ? ' top-movers-row--clickable' : ''}`}
    >
      <div className="top-movers-label">
        {showTicker && <span className="top-movers-ticker">{ticker}</span>}
        <span className="top-movers-name">{name}</span>
        {row.price && +row.price > 0 && (
          <span className="top-movers-price">
            {fPrice(unitPrice, priceDigits(row.asset_class))} {unitCcy}
          </span>
        )}
      </div>
      <div className="top-movers-track">
        <div className={`top-movers-bar top-movers-bar--${tone}`} style={{ width: `${barPct}%` }} />
      </div>
      <div className={`top-movers-amount ${tone}`}>
        <span className="money">
          {pl >= 0 ? '+' : ''}
          {fBase(pl)}
        </span>
        <span className="top-movers-pct">{fPct(pct, { sign: true })}</span>
      </div>
    </EntityRow>
  )
}
