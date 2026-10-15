import type { ReactNode } from 'react'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
}

export interface SegmentedControlProps<T extends string> {
  options: SegmentedOption<T>[]
  value: T
  onChange: (v: T) => void
  ariaLabel?: string
  /** Focus the active segment when the control mounts (e.g. a form step's first control). */
  autoFocus?: boolean
  /** `primary` fills the selected segment with the primary color (a form's mode choice). */
  tone?: 'neutral' | 'primary'
}

/**
 * SegmentedControl — the pill tab group (derives from .intel-wl-tabs, already
 * copy-pasted in ~4 places). Arrow keys move between segments; the active
 * segment is the only one in the tab order via roving tabindex.
 */
export function SegmentedControl<T extends string>({
  options,
  value,
  onChange,
  ariaLabel,
  autoFocus = false,
  tone = 'neutral',
}: SegmentedControlProps<T>) {
  const move = (dir: 1 | -1) => {
    const idx = options.findIndex((o) => o.value === value)
    const next = options[(idx + dir + options.length) % options.length]
    if (next) onChange(next.value)
  }
  return (
    <div
      className={`ui-segmented${tone === 'primary' ? ' ui-segmented--primary' : ''}`}
      role="tablist"
      aria-label={ariaLabel}
    >
      {options.map((o) => {
        const active = o.value === value
        return (
          <button
            key={o.value}
            type="button"
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            autoFocus={autoFocus && active}
            className={`ui-segmented__seg${active ? ' ui-segmented__seg--active' : ''}`}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => {
              if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
                e.preventDefault()
                move(1)
              } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
                e.preventDefault()
                move(-1)
              }
            }}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
