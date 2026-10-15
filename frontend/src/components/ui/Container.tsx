import type { HTMLAttributes, ReactNode } from 'react'

export type ContainerSize = 'default' | 'narrow' | 'wide' | 'full'

export interface ContainerProps extends Omit<HTMLAttributes<HTMLDivElement>, 'className'> {
  /** Max content width: default 1200 / narrow 760 / wide 1440 / full 100%. */
  size?: ContainerSize
  className?: string
  children: ReactNode
}

/**
 * Container — the max-width content well (RFC §2). Caps + centers a tab's
 * content so wide screens stop running edge-to-edge (the "empty wide screen"
 * feel) without changing narrow/desktop rendering. Rendered INSIDE
 * `.app-layout` (it does not replace the layout's padding); a tab that doesn't
 * adopt it renders exactly as today, so this is purely additive.
 *
 *   default = 1200  · narrow = 760  · wide = 1440 (table-heavy tabs)  · full = opt-out
 */
export function Container({ size = 'default', className, children, ...rest }: ContainerProps) {
  const classes = ['ui-container', `ui-container--${size}`, className || '']
    .filter(Boolean)
    .join(' ')
  return (
    <div className={classes} {...rest}>
      {children}
    </div>
  )
}
