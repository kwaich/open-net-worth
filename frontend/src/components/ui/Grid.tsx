import type { CSSProperties, ReactNode } from 'react'
import type { SpaceToken } from './tokens'
import { spaceVar } from './tokens'

export interface GridProps {
  /** Min card px before the row wraps; default 280. */
  min?: number
  /** Gap between cards; default `--space-6` (16px). */
  gap?: SpaceToken
  className?: string
  style?: CSSProperties
  children: ReactNode
}

/**
 * Grid — the responsive card grid that STRETCHES its children to fill the row
 * (RFC §2). Uses `auto-fit` (NOT `auto-fill`) so empty tracks collapse and the
 * cards grow to fill the width — this is the fix for the Skills empty-space bug
 * where 3 `auto-fill` cards left a ragged void. The `min(<min>, 100%)` guard
 * keeps a single card from overflowing a narrow phone. Equal-height rows are
 * free (grid items stretch); pin a card's action row with `margin-top:auto`.
 */
export function Grid({
  min = 280,
  gap = '--space-6',
  className,
  style: styleProp,
  children,
}: GridProps) {
  const style = {
    '--grid-min': `${min}px`,
    '--grid-gap': spaceVar(gap),
    ...styleProp,
  } as CSSProperties
  const classes = ['ui-grid', className || ''].filter(Boolean).join(' ')
  return (
    <div className={classes} style={style}>
      {children}
    </div>
  )
}
