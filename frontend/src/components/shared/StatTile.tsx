/**
 * StatTile — compact stat tile matching the Plan-redesign prototype's
 * FireStat / ReadStat / ScenarioStats card (sentence-case 11.5px label +
 * 21px/600 value, optional inline InfoPopover, optional tone color).
 *
 * Replaces the kit `SummaryBar` on the Projections/Retirement panels, whose
 * uppercase labels + oversized values don't match the redesign typography.
 */
import type { ReactNode } from 'react'
import { InfoPopover } from '../ui/InfoPopover'

export type StatTileTone = 'pos' | 'neg' | 'warn'

export interface StatTileProps {
  label: string
  value: ReactNode
  /** Tone-colors the value. */
  tone?: StatTileTone
  /** Inline click-to-open explanation next to the label. */
  info?: string
  /** Extra content under the value (e.g. a progress bar). */
  children?: ReactNode
}

export function StatTile({ label, value, tone, info, children }: StatTileProps) {
  return (
    <div className="stat-tile">
      <div className="stat-tile-label">
        <span>{label}</span>
        {info ? <InfoPopover text={info} /> : null}
      </div>
      <div className={'stat-tile-value num' + (tone ? ` stat-tile-value--${tone}` : '')}>
        {value}
      </div>
      {children}
    </div>
  )
}

/** Responsive grid wrapper — collapses tiles from N-up to fewer columns as width shrinks. */
export function StatTileRow({ children }: { children: ReactNode }) {
  return <div className="stat-tile-row">{children}</div>
}
