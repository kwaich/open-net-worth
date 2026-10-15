import type { CSSProperties, ReactNode } from 'react'
import type { SpaceToken } from './tokens'
import { spaceVar } from './tokens'

export type ClusterAlign = 'center' | 'start' | 'end' | 'baseline'
export type ClusterJustify = 'start' | 'between' | 'end' | 'center'

export interface ClusterProps {
  /** Gap between items; default `--space-3` (8px). */
  gap?: SpaceToken
  align?: ClusterAlign
  justify?: ClusterJustify
  /** Wrap onto multiple rows; default true (the toolbar-overflow fix). */
  wrap?: boolean
  className?: string
  children: ReactNode
}

const ALIGN: Record<ClusterAlign, string> = {
  center: 'center',
  start: 'flex-start',
  end: 'flex-end',
  baseline: 'baseline',
}

const JUSTIFY: Record<ClusterJustify, string> = {
  start: 'flex-start',
  between: 'space-between',
  end: 'flex-end',
  center: 'center',
}

/**
 * Cluster — a wrapping horizontal group (RFC §2): toolbars, button rows, chip
 * rows. Replaces ad-hoc `style={{display:'flex',gap:8}}`. `wrap` + `min-width:0`
 * is the standard fix for toolbar overflow on narrow screens.
 */
export function Cluster({
  gap = '--space-3',
  align = 'center',
  justify = 'start',
  wrap = true,
  className,
  children,
}: ClusterProps) {
  const style = {
    '--cluster-gap': spaceVar(gap),
    alignItems: ALIGN[align],
    justifyContent: JUSTIFY[justify],
    flexWrap: wrap ? 'wrap' : 'nowrap',
  } as CSSProperties
  const classes = ['ui-cluster', className || ''].filter(Boolean).join(' ')
  return (
    <div className={classes} style={style}>
      {children}
    </div>
  )
}
