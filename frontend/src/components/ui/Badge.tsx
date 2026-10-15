import type { ReactNode } from 'react'

export type BadgeTone = 'pos' | 'neg' | 'neutral' | 'muted' | 'warn'

export interface BadgeProps {
  children: ReactNode
  tone?: BadgeTone
}

/** Badge — status / P&L pill (derives from .pl-badge tones). */
export function Badge({ children, tone = 'neutral' }: BadgeProps) {
  return <span className={`ui-badge ui-badge--${tone}`}>{children}</span>
}
