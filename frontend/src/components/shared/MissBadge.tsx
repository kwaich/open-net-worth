import { fBaseWhole } from '../../utils/format'

export interface MissBadgeProps {
  /** Signed delta value. Positive = above plan, negative = below plan. */
  n: number
}

/**
 * Signed-value tone pill for the plan-vs-actual month table.
 * Renders a compact currency badge tinted green (pos) or red (neg) based on
 * the sign of `n`. Uses the global base currency via `fBase` — no per-instance
 * currency prop (correction 13).
 */
export function MissBadge({ n }: MissBadgeProps) {
  const pos = n >= 0
  const modifier = pos ? 'miss-badge--pos' : 'miss-badge--neg'
  const prefix = pos ? '+' : '−'
  return (
    <span className={`miss-badge ${modifier} num`}>
      {prefix}
      {fBaseWhole(Math.abs(n))}
    </span>
  )
}
