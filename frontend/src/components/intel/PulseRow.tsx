import type { ReactNode } from 'react'
import { TREND_PERIODS } from '../../hooks/usePulsePeriod'
import { SegmentedControl } from '../ui'
import { fPrice, getCurrencySymbol } from '../../utils/format'
import { Bell } from '../../ui/icons'
import { AssetLink } from './AssetLink'
import { Explain } from './Explain'
import { fSignedBase } from './storyMeta'
import { NextCell, TodayCell, TrendCell } from './PulseCells'
import { FINDING_HELP, toneColor } from './pulseHelp'
import type { TrendPeriod, WatchPulseRow } from '../../types/watchPulse'

// One pulse row and the column head above it — the TickerDesk's rows on My holdings and on each
// watchlist tab, so the header labels sit exactly over their figures.

function ContextLine({ row }: { row: WatchPulseRow }) {
  const pe = row.forward_pe ? `fwd P/E ${row.forward_pe.toFixed(0)}` : null
  const first = row.position ? (
    <>
      {row.position.weight.toFixed(1)}% of your investments ·{' '}
      <span style={{ color: toneColor(row.position.day_pl) }}>
        {fSignedBase(row.position.day_pl)} today
      </span>
    </>
  ) : (
    row.fit?.note
  )
  if (!first && !pe && !row.thesis) return null
  return (
    <>
      {(first || pe) && (
        <div className="intel-pulse-fit">
          {first}
          {first && pe ? ' · ' : ''}
          {pe && (
            <Explain className="" text="Price ÷ next year's expected earnings per share">
              {pe}
            </Explain>
          )}
        </div>
      )}
      {row.thesis && (
        <div className="intel-pulse-thesis" title="Your thesis">
          “{row.thesis}”
        </div>
      )}
    </>
  )
}

interface RowProps {
  row: WatchPulseRow
  period: TrendPeriod
  benchSymbol?: string | null
  onEditAlerts?: (row: WatchPulseRow) => void
  /** Tag a name you own (a list tab mixes owned and watched names; on Holdings all are owned). */
  markOwned?: boolean
  /** Extra content under the row — the desk puts the name's own stories here. */
  children?: ReactNode
  className?: string
}

export function PulseColHead({
  period,
  onPeriod,
}: {
  period: TrendPeriod
  onPeriod: (p: TrendPeriod) => void
}) {
  return (
    <div className="intel-pulse-grid intel-pulse-colhead">
      <span aria-hidden="true">Ticker</span>
      <span className="intel-pulse-colhead-trend">
        <span aria-hidden="true">Trend</span>
        <span className="intel-pulse-period">
          <SegmentedControl
            ariaLabel="Trend period"
            value={period}
            onChange={onPeriod}
            options={TREND_PERIODS.map((p) => ({ value: p, label: p }))}
          />
        </span>
      </span>
      <span className="intel-pulse-colhead-next" aria-hidden="true">
        Next
      </span>
      <span className="intel-pulse-colhead-num" aria-hidden="true">
        Today
      </span>
      <span />
    </div>
  )
}

export function PulseRow({
  row,
  period,
  benchSymbol,
  onEditAlerts,
  markOwned,
  children,
  className,
}: RowProps) {
  const sym = getCurrencySymbol(row.currency || undefined)
  const chips = row.findings.slice(0, 3)
  return (
    <div className={`intel-pulse-row${className ? ` ${className}` : ''}`}>
      <div className="intel-pulse-grid">
        <AssetLink
          instrumentId={row.instrument_id || undefined}
          ticker={row.ticker}
          label={row.name}
          className="intel-pulse-id"
        >
          <span className="intel-pulse-idtop">
            <span className="intel-pulse-sym">{row.ticker}</span>
            <span className="intel-pulse-price money">
              {sym}
              {fPrice(row.price)}
            </span>
            {markOwned && row.position && <span className="intel-pulse-owned">Owned</span>}
          </span>
          <span className="intel-pulse-name">{row.name}</span>
        </AssetLink>
        <TrendCell row={row} period={period} sym={sym} benchSymbol={benchSymbol} />
        <NextCell row={row} />
        <TodayCell row={row} />
        {onEditAlerts ? (
          <button
            type="button"
            className="intel-pulse-bell"
            onClick={() => onEditAlerts(row)}
            title={`Price alerts for ${row.ticker}`}
            aria-label={`Price alerts for ${row.ticker}`}
          >
            <Bell size={13} />
          </button>
        ) : (
          <span />
        )}
      </div>
      {chips.length > 0 && (
        <div className="intel-pulse-chips">
          {chips.map((f) => (
            <Explain
              key={f.kind}
              className={`intel-pulse-chip intel-pulse-chip--${f.tone}`}
              text={FINDING_HELP[f.kind] ?? f.text}
            >
              {f.text}
            </Explain>
          ))}
        </div>
      )}
      <ContextLine row={row} />
      {children}
    </div>
  )
}
