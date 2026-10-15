import { useState } from 'react'
import type { HTMLAttributes, ReactNode } from 'react'
import { ChevronDown } from '../../ui/icons'

export interface PanelProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  title?: ReactNode
  /** Muted meta line directly under the title row, left-aligned (first body child). */
  subtitle?: ReactNode
  /** Count of a countable collection, rendered as a muted pill next to the title. */
  badge?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
  /** When true the whole head row toggles the body open/closed (chevron on the right). */
  collapsible?: boolean
  /** Initial collapsed state for a `collapsible` panel (ignored otherwise). */
  defaultCollapsed?: boolean
  /** The card drops its own padding and children compose it from sections — `.panel-section`
   *  (padded) and `.panel-section--flush` (edge to edge: a table, a tile grid). The card's
   *  border then closes a flush section instead of a blank margin floating around it. */
  flush?: boolean
}

/**
 * Panel — section card (.panel) with an optional uppercase title, an inline
 * count `badge` beside the title, a left-aligned `subtitle` meta line under the
 * title row, and a right-aligned `actions` slot, so panels stop hand-rolling
 * section headings. `subtitle`/`badge` are additive — callers that pass only
 * `title`/`actions` render exactly as before.
 *
 * `collapsible` turns the whole head row into a toggle button (a right-aligned
 * ChevronDown that rotates 180° when open, aria-expanded) that shows/hides the
 * body; `defaultCollapsed` sets the initial state. Collapsible panels don't use
 * the `actions` slot (the chevron owns the right edge), matching the monitoring
 * summary bar's style.
 */
export function Panel({
  title,
  subtitle,
  badge,
  actions,
  children,
  className,
  collapsible,
  defaultCollapsed,
  flush,
  ...rest
}: PanelProps) {
  const [collapsed, setCollapsed] = useState(!!defaultCollapsed)
  const isCollapsed = !!collapsible && collapsed
  const classes = [
    'panel',
    isCollapsed ? 'panel--collapsed' : '',
    flush ? 'panel--flush' : '',
    className || '',
  ]
    .filter(Boolean)
    .join(' ')
  const heading = (
    <div className="ui-panel__heading">
      <div className="ui-panel__titlerow">
        {title ? <h2 className="ui-panel__title">{title}</h2> : <span />}
        {badge != null && <span className="ui-panel__badge">{badge}</span>}
      </div>
      {subtitle && <div className="ui-panel__subtitle">{subtitle}</div>}
    </div>
  )
  if (collapsible) {
    return (
      <section className={classes} {...rest}>
        <button
          type="button"
          className="ui-panel__head ui-panel__head--toggle"
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((c) => !c)}
        >
          {heading}
          <ChevronDown
            size={12}
            strokeWidth={1.5}
            aria-hidden="true"
            className={`ui-panel__chevron${collapsed ? '' : ' ui-panel__chevron--open'}`}
          />
        </button>
        {!collapsed && children}
      </section>
    )
  }
  const hasHead = title || actions || subtitle || badge != null
  return (
    <section className={classes} {...rest}>
      {hasHead && (
        <div className="ui-panel__head">
          {heading}
          {actions && <div className="ui-panel__actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  )
}
