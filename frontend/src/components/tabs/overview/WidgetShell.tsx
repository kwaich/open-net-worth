// Overview v2 — the shared <WidgetShell> content shell every card renders
// through (predefined AND Lucius-generated alike — see types.ts), wrapping the
// DS Panel so title-override, the meta suffix, the optional chart-type toggle,
// and the fill/fit body all behave identically everywhere. Ported from the
// handoff's `widget_shell.jsx` (`OvWidget.Widget`).
//
// Grid chrome (drag handle, resize handle, the kebab menu) lives one level up
// in GridCard.tsx — this component is content-only, exactly like the prototype's.

import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { MoreVertical } from 'lucide-react'
import { Panel } from '../../ui'
import { KebabMenu } from '../planning/KebabMenu'
import { ChartToggleMenu } from './ChartToggleMenu'
import { useGridItemContext } from './gridItemContext'

export interface WidgetChartToggle<T extends string = string> {
  value: T
  options: Array<{ value: T; label: ReactNode }>
  onChange: (v: T) => void
}

export interface WidgetShellProps {
  /** Custom rename override — wins over `base`+`meta` when present. */
  title?: string
  /** The widget's own default title text. */
  base?: ReactNode
  /** Optional glyph before the title (e.g. the Lucius spark mark). */
  lead?: ReactNode
  /** Small trailing detail after the title (a total, a timestamp) — kept OUT
   * of the title string itself so a rename can never eat a live figure. */
  meta?: ReactNode
  subtitle?: ReactNode
  chart?: WidgetChartToggle
  /** Extra header actions, merged left of the chart toggle. */
  actions?: ReactNode
  /** Pinned under the body — an "All …" link, e.g. */
  footer?: ReactNode
  /** true → body is a flex-fill region (charts); default = a fit/scroll region (lists, prose). */
  fill?: boolean
  bodyAlign?: 'center'
  bodyClass?: string
  /** A fixed strip pinned above the scrolling body (e.g. a Timeline axis). */
  subhead?: ReactNode
  children: ReactNode
}

// Double-click (any mode — mirrors the handoff's `titleInput`) swaps the
// title TEXT for an inline `<input>` in the exact same DOM slot, so it
// inherits the title's own typography for free (no overlay, no measured
// positioning). Blank or a value matching the def's own default clears the
// rename override back to the default.
function WidgetTitle({
  text,
  meta,
  lead,
  editable,
  placeholder,
  onCommit,
}: {
  text: ReactNode
  meta?: ReactNode
  lead?: ReactNode
  editable?: boolean
  placeholder?: string
  onCommit?: (value: string | undefined) => void
}) {
  const [renaming, setRenaming] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (renaming) inputRef.current?.focus()
  }, [renaming])

  if (renaming && editable && onCommit) {
    const commit = (raw: string) => {
      const v = raw.trim()
      onCommit(!v || v === placeholder ? undefined : v)
      setRenaming(false)
    }
    return (
      <input
        ref={inputRef}
        className="ov-title-edit"
        defaultValue={typeof text === 'string' ? text : ''}
        placeholder={placeholder}
        aria-label="Widget title"
        onClick={(e) => e.stopPropagation()}
        onPointerDown={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}
        onBlur={(e) => commit(e.currentTarget.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur()
          if (e.key === 'Escape') setRenaming(false)
        }}
      />
    )
  }
  return (
    <span
      className={editable ? 'ov-widget-title-rename' : undefined}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 7, minWidth: 0 }}
      title={editable ? 'Double-click to rename' : undefined}
      onDoubleClick={
        editable
          ? (e) => {
              e.preventDefault()
              e.stopPropagation()
              setRenaming(true)
            }
          : undefined
      }
    >
      {lead}
      {/* Title never shrinks below its natural size — meta absorbs the squeeze. */}
      <span
        style={{
          flex: '0 0 auto',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {text}
      </span>
      {meta != null && meta !== '' && (
        <span
          className="ov-t-meta"
          style={{
            flex: '1 1 auto',
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          • {meta}
        </span>
      )}
    </span>
  )
}

export function WidgetShell({
  title,
  base,
  lead,
  meta,
  subtitle,
  chart,
  actions,
  footer,
  fill,
  bodyAlign,
  bodyClass,
  subhead,
  children,
}: WidgetShellProps) {
  const ctx = useGridItemContext()
  // Default lead = the widget's own def icon (item 10) — a widget that passes
  // its own `lead` explicitly (e.g. the daily briefing's Lucius spark) wins.
  const effectiveLead = lead ?? ctx?.icon
  const hasOverride = title != null && title !== ''
  const placeholder = typeof base === 'string' ? base : undefined
  const head = (
    <WidgetTitle
      text={hasOverride ? title : base}
      lead={effectiveLead}
      meta={hasOverride ? undefined : meta}
      editable={!!ctx?.onRenameTitle}
      placeholder={placeholder}
      onCommit={ctx?.onRenameTitle}
    />
  )
  // Compact dropdown, not SegmentedControl — never wraps onto its own line.
  const toggle = chart ? <ChartToggleMenu ariaLabel="Chart type" {...chart} /> : null
  const mobileMenuItems =
    ctx && !ctx.editing
      ? [
          ...(ctx.primaryMenuItems || []),
          ...(ctx.onOpenFullscreen
            ? [{ label: 'Full screen', onClick: ctx.onOpenFullscreen }]
            : []),
          ...(ctx.onToggleCollapse
            ? [
                {
                  label: ctx.collapsed ? 'Expand' : 'Collapse',
                  onClick: ctx.onToggleCollapse,
                },
              ]
            : []),
          ...(ctx.menuItems || []),
        ]
      : []
  const mobileMenu =
    mobileMenuItems.length > 0 ? (
      <span className="ov-widget-header-kebab">
        {/* Portaled: the ⋯ lives inside the card, whose overflow clipping hid the popover. */}
        <KebabMenu
          portal
          items={mobileMenuItems}
          label="Widget options"
          icon={<MoreVertical size={16} strokeWidth={1.8} aria-hidden="true" />}
        />
      </span>
    ) : null
  const headActions =
    actions || toggle || mobileMenu ? (
      <span className="ov-responsive-toolbar">
        {actions}
        {toggle}
        {mobileMenu}
      </span>
    ) : undefined
  return (
    <Panel className="ov-widget-panel" title={head} subtitle={subtitle} actions={headActions}>
      {subhead != null && subhead !== '' && <div className="ov-subhead-fixed">{subhead}</div>}
      <div
        className={
          (fill ? 'ov-fill' : 'ov-fit') +
          (bodyClass ? ' ' + bodyClass : '') +
          (bodyAlign === 'center' ? ' ov-body-center' : '')
        }
      >
        {children}
      </div>
      {footer}
    </Panel>
  )
}
