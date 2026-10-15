import type { CSSProperties, ReactNode } from 'react'
import type { SpaceToken } from './tokens'
import { spaceVar } from './tokens'

export type StackAlign = 'stretch' | 'start' | 'center' | 'end'

export interface StackProps {
  /** Vertical gap between children; default `--space-6` (16px). */
  gap?: SpaceToken
  align?: StackAlign
  className?: string
  children: ReactNode
}

const ALIGN: Record<StackAlign, string> = {
  stretch: 'stretch',
  start: 'flex-start',
  center: 'center',
  end: 'flex-end',
}

/**
 * Stack — vertical rhythm (RFC §2). One gap token replaces the scattered
 * `margin-bottom:18px` repetitions between panels. `min-width:0` is baked in so
 * a long-token child can shrink instead of clipping.
 */
export function Stack({ gap = '--space-6', align = 'stretch', className, children }: StackProps) {
  const style = {
    '--stack-gap': spaceVar(gap),
    alignItems: ALIGN[align],
  } as CSSProperties
  const classes = ['ui-stack', className || ''].filter(Boolean).join(' ')
  return (
    <div className={classes} style={style}>
      {children}
    </div>
  )
}
