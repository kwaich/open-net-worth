import { useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

export interface CollapsibleSectionProps {
  /** Section heading (rendered in the uppercase Panel-title style). */
  title: ReactNode
  /** Optional right-aligned content in the header row (count, badge, etc.). */
  actions?: ReactNode
  /** Whether the body is open on first render. Default false (collapsed). */
  defaultOpen?: boolean
  /** Externally controlled open state. When provided the component is controlled
   *  and internal toggle state is bypassed — only `onOpenChange` is called. */
  open?: boolean
  /** Notified whenever the open state changes — so a parent that draws into a
   *  canvas mounted in the body can (re)create the chart once it's actually in the
   *  DOM (a chart effect keyed only on data never re-runs on expand otherwise). */
  onOpenChange?: (open: boolean) => void
  className?: string
  children: ReactNode
}

/**
 * CollapsibleSection — a kit-styled disclosure: a clickable header row (title +
 * chevron, keyboard-toggle, `aria-expanded`) over a `<Panel>`-shaped body that
 * mounts only when open. Generalizes the WealthBriefing collapse pattern so a
 * screen can park secondary/heavy content behind a header row until the user
 * asks for it. Tokens only — no raw px/hex.
 */
export function CollapsibleSection({
  title,
  actions,
  defaultOpen = false,
  open,
  onOpenChange,
  className,
  children,
}: CollapsibleSectionProps) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen)
  const isOpen = open !== undefined ? open : internalOpen
  const toggle = () => {
    if (open !== undefined) {
      onOpenChange?.(!open)
    } else {
      setInternalOpen((o) => {
        const next = !o
        onOpenChange?.(next)
        return next
      })
    }
  }
  const classes = ['panel', 'ui-collapsible', className || ''].filter(Boolean).join(' ')

  return (
    <section className={classes}>
      <div className="ui-collapsible__head">
        <button
          type="button"
          className="ui-collapsible__toggle"
          aria-expanded={isOpen}
          onClick={toggle}
        >
          <ChevronRight className="ui-collapsible__chevron" size={16} strokeWidth={2} aria-hidden />
          <span className="ui-panel__title ui-collapsible__title">{title}</span>
        </button>
        {actions && <div className="ui-panel__actions">{actions}</div>}
      </div>
      {isOpen && <div className="ui-collapsible__body">{children}</div>}
    </section>
  )
}
