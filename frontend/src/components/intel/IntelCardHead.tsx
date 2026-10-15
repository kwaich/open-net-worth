import type { ReactNode } from 'react'
import { Spark } from '../../lucius/identity'
import { fTime } from '../../utils/format'

// The one header row every Intel tab card shares: icon · title · (info) (badge) … Ask Lucius ·
// time. The Holdings brief, the Watchlist pulse and Topics you follow use it in every state, so
// the tabs read alike.

export function IntelCardHead({
  icon,
  title,
  info,
  badge,
  ask,
  updatedAt,
}: {
  icon: ReactNode
  title: string
  /** An explainer beside the title (an InfoPopover). */
  info?: ReactNode
  /** A count pill after the title (e.g. "2 to decide"). */
  badge?: ReactNode
  /** Ask Lucius about this card: its accessible name and the question it sends. */
  ask?: { label: string; onAsk: () => void } | null
  updatedAt?: string | null
}) {
  const time = updatedAt ? fTime(new Date(updatedAt)) : ''
  return (
    <div className="intel-brief-head">
      <span className="lx-mark">{icon}</span>
      <span className="intel-brief-title">{title}</span>
      {info}
      {badge}
      <span className="intel-brief-head-gap" aria-hidden="true" />
      {ask && (
        <button
          type="button"
          className="intel-brief-ask intel-brief-ask--lucius"
          aria-label={ask.label}
          onClick={ask.onAsk}
        >
          <span className="intel-brief-ask-label">Ask Lucius</span>
          <Spark className="intel-brief-ask-spark" size={13} filled ariaHidden="true" />
        </button>
      )}
      {time && <span className="intel-brief-updated">{time}</span>}
    </div>
  )
}
