// Overview v2 — the shared honest-empty-state block for a widget that has no
// data yet, or a capability that is genuinely deferred (never a fabricated
// number — CLAUDE.md's "may say coming soon, must never lie with a broken
// screen"). Every widget-group lane should reach for this instead of hand-
// rolling its own empty/coming-soon copy, so the tone is consistent everywhere.

import type { ReactNode } from 'react'
import { Skeleton, Text } from '../../ui'
import { useFitContentWhen } from './gridItemContext'

/** `title` is for a message about the widget ITSELF ("This widget hit an error."); a widget's
 * own empty state passes only `detail` — the card already shows its name above. */
export function WidgetPlaceholder({ title, detail }: { title?: string; detail?: ReactNode }) {
  // An empty state is one or two lines: the card fits them instead of holding the height its
  // content would need (a fixed-height card, or a row-mate's, left a tall box with one line).
  useFitContentWhen(true)
  return (
    <div className="ov-widget-placeholder">
      {title && (
        <Text as="p" size="sm" tone="muted" weight={600}>
          {title}
        </Text>
      )}
      {detail && (
        <Text as="p" size={title ? 'xs' : 'sm'} tone="faint">
          {detail}
        </Text>
      )}
    </div>
  )
}

/** What a data widget shows until its read resolves — never its empty message, which would
 * claim "nothing due" / "all clear" about data it has not seen yet. */
export function WidgetSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="ov-widget-skeleton" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} height={14} width={i === rows - 1 ? '60%' : '100%'} />
      ))}
    </div>
  )
}
