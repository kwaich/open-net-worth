import { useId, useState, useCallback } from 'react'

export interface StatInfoProps {
  /** Explanatory text shown in the hover/focus popover. */
  text: string
  /** Optional CSS class name added to the wrapper span. */
  className?: string
}

/**
 * StatInfo — serif-italic `i` tooltip for dense stat labels.
 *
 * Distinct from InfoPopover (which is click-to-open, generic).
 * StatInfo uses hover + focus (keyboard accessible) and a Georgia
 * serif-italic glyph to signal "contextual definition", matching the
 * investments redesign prototype stat-label affordance.
 *
 * CSS: .stat-info-wrap / .stat-info-btn / .stat-info-pop — defined in index.css.
 * (Uniquely named so they don't collide with PerformanceTab's local
 * .stat-info / .stat-pop hover-tooltip in design.css.)
 */
export function StatInfo({ text, className }: StatInfoProps) {
  const [visible, setVisible] = useState(false)
  const id = useId()

  const show = useCallback(() => setVisible(true), [])
  const hide = useCallback(() => setVisible(false), [])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') setVisible(false)
  }, [])

  return (
    <span className={`stat-info-wrap${className ? ` ${className}` : ''}`}>
      <button
        type="button"
        className="stat-info-btn"
        role="button"
        tabIndex={0}
        aria-label="More information"
        aria-expanded={visible}
        aria-controls={id}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onKeyDown={handleKeyDown}
      >
        i
      </button>
      {visible && (
        <span id={id} role="tooltip" className="stat-info-pop">
          {text}
        </span>
      )}
    </span>
  )
}
