// plan/budgetCards.tsx — mobile (phone) card shells for the Budget sub-tab. Pure presentational
// wrappers: CategoryRow/GroupBlock/OutsideEnvelopes (budget.tsx) compute every bit of state and
// pre-build the leaf JSX (ColorDot, Kebab, ActivityCell, AvailCell, EnvCatsBadge, the target
// bar, the assign editor — the SAME instances the desktop `.pl-trow` row uses) and pass them in
// as props here. These components only re-lay that JSX into `.pl-mcard`/`.pl-mgroup-head`
// stacks — no new state, no re-derived business logic, no drag-to-reorder (desktop-only).
import type { ReactNode, CSSProperties, MouseEvent } from 'react'

export function MobileNumsGrid({
  assignedLabel = 'ASSIGNED',
  activityLabel = 'ACTIVITY',
  availableLabel = 'AVAILABLE',
  assigned,
  activity,
  available,
}: {
  assignedLabel?: string
  activityLabel?: string
  availableLabel?: string
  assigned: ReactNode
  activity: ReactNode
  available: ReactNode
}) {
  return (
    <div className="pl-mcard-nums">
      <div className="pl-mnum">
        <span className="pl-mnum-lbl">{assignedLabel}</span>
        {assigned}
      </div>
      <div className="pl-mnum">
        <span className="pl-mnum-lbl">{activityLabel}</span>
        {activity}
      </div>
      <div className="pl-mnum">
        <span className="pl-mnum-lbl">{availableLabel}</span>
        {available}
      </div>
    </div>
  )
}

export interface MobileEnvelopeCardProps {
  className?: string
  style?: CSSProperties
  draggable?: boolean
  mentionProps?: Record<string, unknown>
  onClick?: (e: MouseEvent<HTMLDivElement>) => void
  dot: ReactNode
  name: ReactNode
  /** The "open commitment" deep-link icon-chip a Commitments-synced row carries next to its name. */
  openCommitmentChip?: ReactNode
  /** EnvCatsBadge, or the "0 categories" hint — the two are mutually exclusive. */
  badgeOrHint?: ReactNode
  /** Omitted for a row with no options menu at all (Outside envelopes has none). */
  kebab?: ReactNode
  /** The YNAB-style target progress bar — same component, only rendered when the caller's own
   *  condition (mirroring the desktop row) says so. */
  targetBar?: ReactNode
  statusLine?: ReactNode
  /** Cover/Fund/Move (or an Outside-envelope row's own action) — forced static+visible; the
   *  desktop `.pl-caction` this reuses is hover-revealed and absolutely positioned, meaningless
   *  on a touch device. */
  actionButton?: ReactNode
  nums: ReactNode
}
export function MobileEnvelopeCard({
  className,
  style,
  draggable,
  mentionProps,
  onClick,
  dot,
  name,
  openCommitmentChip,
  badgeOrHint,
  kebab,
  targetBar,
  statusLine,
  actionButton,
  nums,
}: MobileEnvelopeCardProps) {
  return (
    <div
      className={'pl-mcard' + (className ? ' ' + className : '')}
      style={style}
      draggable={draggable}
      {...mentionProps}
      onClick={onClick}
    >
      <div className="pl-mcard-head">
        {dot}
        <span className="pl-mcard-name">
          {name}
          {openCommitmentChip}
        </span>
        {badgeOrHint}
        {kebab}
      </div>
      {targetBar}
      {statusLine && <div className="pl-mcard-status">{statusLine}</div>}
      {nums}
      {actionButton && <div className="pl-mcard-actionwrap">{actionButton}</div>}
    </div>
  )
}

// Group / Outside-envelopes header — chevron+name+collapse handler lives in `name` already
// (the same `.pl-gtoggle` cluster the desktop row builds); `trailing` is the Synced pill OR the
// add(+)+Kebab cluster, exactly the desktop conditions.
//
// `onToggle` (omitted for the collapse-less "Outside envelopes" header) fires on a tap ANYWHERE
// on the whole card — toprow AND the numbers row — not just the small chevron button inside
// `name` (owner ask: the chevron alone was too small a target on a phone, and tapping the
// numbers did nothing). The rename button, add(+) button, and Kebab trigger inside `name`/
// `trailing` all stop their own click's propagation, so tapping one of those still does ONLY
// that action rather than also toggling.
export function MobileGroupHead({
  className,
  name,
  count,
  trailing,
  nums,
  onToggle,
  collapsed,
}: {
  className?: string
  name: ReactNode
  count: ReactNode
  trailing?: ReactNode
  nums: ReactNode
  onToggle?: () => void
  collapsed?: boolean
}) {
  return (
    <div
      className={'pl-grow pl-mgroup-head' + (className ? ' ' + className : '')}
      onClick={onToggle}
      onKeyDown={
        onToggle
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onToggle()
              }
            }
          : undefined
      }
      role={onToggle ? 'button' : undefined}
      tabIndex={onToggle ? 0 : undefined}
      aria-expanded={onToggle ? !collapsed : undefined}
    >
      <div className="pl-mgroup-toprow">
        <span className="pl-mgroup-name-slot">{name}</span>
        {count}
        {trailing}
      </div>
      {nums}
    </div>
  )
}
