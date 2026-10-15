import { useState, type CSSProperties } from 'react'
import { skeletonPhaseMs } from './skeletonHandoff'

export interface SkeletonProps {
  /** Width — number → px, string passed through (e.g. "60%"). Defaults to 100%. */
  width?: number | string
  /** Height — number → px, string passed through. */
  height?: number | string
  /** Corner radius — number → px, string passed through. Ignored when `circle`. */
  radius?: number | string
  /** Render a perfect circle (avatar/dot placeholder). */
  circle?: boolean
  className?: string
  style?: CSSProperties
}

const px = (v: number | string) => (typeof v === 'number' ? `${v}px` : v)

/**
 * Skeleton — a quietly breathing placeholder block. Compose these into a layout-shaped
 * loading state so real content fades into the SAME slots (no layout shift).
 * Animated by `@keyframes skeleton-breathe` in index.css.
 */
export function Skeleton({ width, height, radius, circle, className, style }: SkeletonProps) {
  // Phase-locked to one page-wide clock, so every bar — including one that mounts mid-load —
  // breathes in unison instead of restarting its pulse.
  const [phase] = useState(skeletonPhaseMs)
  const css: CSSProperties = { animationDelay: `${phase}ms`, ...style }
  if (width != null) css.width = px(width)
  if (height != null) css.height = px(height)
  if (circle) css.borderRadius = '50%'
  else if (radius != null) css.borderRadius = px(radius)

  return (
    <span
      className={`ui-skeleton${className ? ` ${className}` : ''}`}
      style={css}
      aria-hidden="true"
    />
  )
}
