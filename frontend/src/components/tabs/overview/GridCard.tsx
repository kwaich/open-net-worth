// Overview v2 — the grid-item wrapper: absolute-positioned on desktop (12-col,
// percentage x/w + pixel y/h), static-stacked on mobile (CSS handles the
// breakpoint switch — see overview.css's `@media (max-width: 640px)`). Hosts
// the customize-mode chrome (whole-card drag, resize handles, the in-card
// Fit · Edit · Remove toolbar) around either a real widget's <WidgetShell> or a
// section divider — the ONE wrapper every grid item uses. View-mode actions are
// NOT here: they live in the widget header's own ⋯ (WidgetShell), never as
// buttons floating over the card on hover.

import type { ReactNode } from 'react'
import { GAP_PX } from './layoutConstants'

/** Resize direction: 1 = grow toward bottom/right, -1 = mirrored (the
 * opposite edge stays pinned), 0 = axis locked. Mirrors the handoff's
 * `startResize(e, wg, dirX, dirY)`. */
export type ResizeDir = -1 | 0 | 1

export interface GridCardProps {
  /** The instance's own id — stamped as `data-instance-id` so the auto-height
   * ResizeObserver (DashboardGrid) can map a measured `.panel` back to the
   * item it belongs to. */
  instanceId?: string
  x: number
  y: number
  w: number
  h: number
  editing: boolean
  /** Content-fit ("prose") widget currently in auto-height mode — relaxes the
   * panel's CSS height so its true content height is measurable, and flags it
   * for the ResizeObserver (item 1 — the auto-height fix). */
  autoHeight?: boolean
  accent?: string | null
  /** Display option (kebab → Display…) — hides the widget's own WidgetShell
   * title row (`.ui-panel__head`) via CSS; the grid-level drag/kebab/resize
   * overlays are unaffected since they're positioned independently. */
  hideTitle?: boolean
  isSection?: boolean
  /** Currently being dragged / resized — CSS-only visual states (item 13). */
  dragging?: boolean
  resizing?: boolean
  /** Landed via Add-widget / Add-section within the last ~1.9s — accent pulse (item 11/20). */
  isNew?: boolean
  /** Collapsed to a slim title-only bar (item 8) — WIDGETS only; a section's
   * own collapse is handled by its caller (DashboardGrid), since a section's
   * "collapsed" body is its member rows, not this card's own children. */
  collapsed?: boolean
  /** Label shown on the collapsed slim bar. */
  collapsedLabel?: ReactNode
  onToggleCollapse?: () => void
  /** Opens the fullscreen detail view (item 7) — undefined for a section. Also
   * the target of Customize mode's own "Edit" button (below) — the handoff's
   * `openDetail` is the SAME function for both entry points. */
  onOpenFullscreen?: () => void
  /** Customize's Edit button — the settings (a bottom sheet on phones). Falls back to
   *  `onOpenFullscreen`. */
  onEdit?: () => void
  /** Customize-mode direct chrome — `.ov-w-ctrls` in the handoff (`app.jsx`
   * ~1112-1119): a Fit/Fixed toggle (autoh widgets only) + Edit + Remove,
   * ALWAYS visible while editing, inside the card's header row. */
  isAutoh?: boolean
  fitOn?: boolean
  onToggleFit?: () => void
  onRemove?: () => void
  onDragPointerDown?: (e: React.PointerEvent) => void
  onResizePointerDown?: (e: React.PointerEvent, dirX: ResizeDir, dirY: ResizeDir) => void
  /** Live "N / 12 cols" readout shown while resizing. */
  resizeReadout?: string
  /** Mobile-only reorder (no drag surface on touch — see the ADR's responsive strategy). */
  onMoveUp?: () => void
  onMoveDown?: () => void
  children: ReactNode
}

const fitIcon = (
  <svg
    width="13"
    height="13"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.9"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M8 7l4-4 4 4M8 17l4 4 4-4M4 12h16" />
  </svg>
)
const fixedIcon = (
  <svg
    width="13"
    height="13"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.9"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <rect x="4" y="4" width="16" height="16" rx="2" />
    <path d="M8 9h8M8 15h8" />
  </svg>
)
const editIcon = (
  <svg
    width="11"
    height="11"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M4 20h4L18.5 9.5a2 2 0 0 0-3-3L5 17v3z" />
    <path d="M13.5 6.5l3 3" />
  </svg>
)
const removeIcon = (
  <svg
    width="11"
    height="11"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.4"
    strokeLinecap="round"
  >
    <line x1="5" y1="5" x2="19" y2="19" />
    <line x1="19" y1="5" x2="5" y2="19" />
  </svg>
)
const expandIcon = (
  <svg
    width="15"
    height="15"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    <path d="M6 9l6 6 6-6" />
  </svg>
)

const RESIZE_HANDLES: Array<{ cls: string; dirX: ResizeDir; dirY: ResizeDir; label: string }> = [
  { cls: 'e-r', dirX: 1, dirY: 0, label: 'Resize width' },
  { cls: 'e-l', dirX: -1, dirY: 0, label: 'Resize width' },
  { cls: 'e-b', dirX: 0, dirY: 1, label: 'Resize height' },
  { cls: 'e-t', dirX: 0, dirY: -1, label: 'Resize height' },
  { cls: 'c-se', dirX: 1, dirY: 1, label: 'Resize' },
  { cls: 'c-sw', dirX: -1, dirY: 1, label: 'Resize' },
  { cls: 'c-ne', dirX: 1, dirY: -1, label: 'Resize' },
  { cls: 'c-nw', dirX: -1, dirY: -1, label: 'Resize' },
]

export function GridCard({
  instanceId,
  x,
  y,
  w,
  h,
  editing,
  autoHeight,
  accent,
  hideTitle,
  isSection,
  dragging,
  resizing,
  isNew,
  collapsed,
  collapsedLabel,
  onToggleCollapse,
  onOpenFullscreen,
  onEdit,
  isAutoh,
  fitOn,
  onToggleFit,
  onRemove,
  onDragPointerDown,
  onResizePointerDown,
  resizeReadout,
  onMoveUp,
  onMoveDown,
  children,
}: GridCardProps) {
  const accentStyle = accent
    ? ({
        '--wtint': accent.startsWith('#')
          ? accent
          : `var(--${accent === 'blue' ? 'accent' : accent === 'green' ? 'good' : accent === 'amber' ? 'warn' : accent === 'violet' ? 'violet' : 'lucius'})`,
      } as React.CSSProperties)
    : undefined

  return (
    <div
      data-instance-id={instanceId}
      className={
        'ov-griditem' +
        (isSection ? ' ov-griditem--section' : '') +
        (editing ? ' ov-griditem--editing' : '') +
        (accent ? ' ov-griditem--accent' : '') +
        (hideTitle ? ' ov-griditem--hide-title' : '') +
        (dragging ? ' ov-griditem--dragging' : '') +
        (resizing ? ' ov-griditem--resizing' : '') +
        (isNew ? ' ov-griditem--new' : '') +
        (autoHeight ? ' ov-griditem--autoh' : '')
      }
      style={{
        left: `calc(${(x / 12) * 100}% + ${GAP_PX / 2}px)`,
        width: `calc(${(w / 12) * 100}% - ${GAP_PX}px)`,
        top: y,
        height: Math.max(0, h - GAP_PX),
        ...accentStyle,
      }}
      // Customize mode: the WHOLE card is the drag surface (mirrors the
      // handoff's `startDrag`, `onPointerDown={editLayout ? ... : undefined}`
      // on the widget's own root div) — not a small dedicated handle icon.
      // Excludes interactive chrome (the Fit/Edit/Remove row, resize handles,
      // the title, any control inside the widget's own content) so clicking
      // those doesn't also start a drag.
      onPointerDown={
        editing && !isSection && onDragPointerDown
          ? (e) => {
              const target = e.target as HTMLElement
              if (
                target.closest(
                  'button, a, input, select, textarea, .ov-chartwrap, .ov-resize-handle, .ui-panel__title, .ov-widget-title, [role="button"]',
                )
              )
                return
              // Cancel the browser's native selection gesture before pointer
              // capture starts. Without this, dragging across widget copy
              // leaves text highlighted after the card is dropped.
              e.preventDefault()
              onDragPointerDown(e)
            }
          : undefined
      }
    >
      {editing && (onOpenFullscreen || onRemove) && (
        <div className="ov-w-ctrls">
          {!isSection && isAutoh && (
            <button
              type="button"
              className="ov-w-ctrl ov-w-ctrl--label ov-w-ctrl--fit"
              onClick={onToggleFit}
              title={fitOn ? 'Switch to fixed height' : 'Fit height to content'}
              aria-label="Toggle height mode"
            >
              {fitOn ? fitIcon : fixedIcon}
              <span>{fitOn ? 'Fit' : 'Fixed'}</span>
            </button>
          )}
          {onOpenFullscreen && (
            <button
              type="button"
              className="ov-w-ctrl ov-w-ctrl--label"
              onClick={onEdit ?? onOpenFullscreen}
              title="Edit widget"
              aria-label="Edit widget"
            >
              {editIcon}
              <span>Edit</span>
            </button>
          )}
          {onRemove && (
            <button
              type="button"
              className="ov-w-remove"
              onClick={onRemove}
              title="Remove widget"
              aria-label="Remove widget"
            >
              {removeIcon}
            </button>
          )}
        </div>
      )}
      {editing && (onMoveUp || onMoveDown) && (
        <div className="ov-card-mobile-move" aria-hidden={false}>
          <button type="button" aria-label="Move up" onClick={onMoveUp} disabled={!onMoveUp}>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 15l6-6 6 6" />
            </svg>
          </button>
          <button type="button" aria-label="Move down" onClick={onMoveDown} disabled={!onMoveDown}>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.4"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M6 9l6 6 6-6" />
            </svg>
          </button>
        </div>
      )}
      {resizing && resizeReadout && <span className="ov-resize-readout">{resizeReadout}</span>}
      <div className="ov-griditem__body">
        {collapsed && !isSection ? (
          <button
            type="button"
            className="ov-griditem-collapsed"
            onClick={onToggleCollapse}
            title="Expand"
          >
            <span className="ov-griditem-collapsed-t">{collapsedLabel}</span>
            {expandIcon}
          </button>
        ) : (
          children
        )}
      </div>
      {editing &&
        !isSection &&
        !collapsed &&
        onResizePointerDown &&
        // In Fit mode, CSS forces the panel's height to its own content
        // (`.ov-griditem--autoh .panel { height: auto }`) regardless of the
        // stored `h` — so a height-axis handle would drag the stored value
        // and shove sibling widgets around while the box itself never
        // visibly moves (the exact bug report this fixes). Mirrors the
        // handoff's own `{!fitMode(wg) && <...height/corner handles...>}` —
        // width-only resize stays available; switch to Fixed height (the
        // Fit/Fixed toggle above) to resize height too.
        RESIZE_HANDLES.filter((r) => r.dirY === 0 || !(isAutoh && fitOn)).map((r) => (
          <button
            key={r.cls}
            type="button"
            className={`ov-resize-handle ov-resize-handle--${r.cls}`}
            aria-label={r.label}
            onPointerDown={(e) => onResizePointerDown(e, r.dirX, r.dirY)}
          />
        ))}
    </div>
  )
}
