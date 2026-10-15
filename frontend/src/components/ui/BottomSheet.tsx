// BottomSheet — the phone presentation for any short option list or action menu: the app's
// standard bottom sheet (shared `kebab-sheet-*` chrome + drag-to-dismiss), portaled to <body>.
//
// Owns the sheet's lifecycle side effects so a caller only decides WHEN it is open: Escape
// dismisses, the page behind is scroll-locked, the first control takes focus. Render it
// conditionally (`{open && <BottomSheet …>}`) — mounting is opening. Extracted from the
// Investments FilterSheet (owner, 2026-09-04: "move date filters to bottom sheet for
// mobile") so the range picker becomes the second user rather than the fifth hand-rolled copy.

import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from '../../ui/icons'
import { useSheetDragToDismiss } from '../../hooks/useSheetDragToDismiss'

export interface BottomSheetProps {
  /** Dialog title, e.g. "Filter holdings" / "Date range". */
  title: string
  onClose: () => void
  children: ReactNode
  /** Actions pinned under the scrolling body (e.g. Discard / Save). */
  footer?: ReactNode
  /** Near-full-height sheet whose body fills it — for content (a chart, a widget) rather than
   *  a short option list. */
  tall?: boolean
  /** One action beside the close button (e.g. "Edit") — instead of a footer bar for a lone
   *  button. */
  headerAction?: ReactNode
}

export function BottomSheet({
  title,
  onClose,
  children,
  footer,
  tall,
  headerAction,
}: BottomSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null)
  const scrimRef = useRef<HTMLDivElement>(null)
  const dragHandlers = useSheetDragToDismiss({
    sheetRef,
    overlayRef: scrimRef,
    onDismiss: onClose,
  })

  // Read through a ref so an inline `() => setOpen(false)` doesn't re-run the lock each
  // render — a second run would capture `prev = 'hidden'` and leave the page locked on close.
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  })
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onCloseRef.current()
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    requestAnimationFrame(() => {
      sheetRef.current?.querySelector<HTMLElement>('button')?.focus()
    })
    return () => {
      document.body.style.overflow = prev
      document.removeEventListener('keydown', onKey)
    }
  }, [])

  return createPortal(
    <>
      <div ref={scrimRef} className="kebab-sheet-scrim" onClick={onClose} aria-hidden="true" />
      <div
        className={'kebab-sheet' + (tall ? ' kebab-sheet--tall' : '')}
        role="dialog"
        aria-label={title}
        ref={sheetRef}
        {...dragHandlers}
      >
        <div className="kebab-sheet-handle-wrap">
          <span className="kebab-sheet-handle" />
        </div>
        <div className="kebab-sheet-head">
          <span className="kebab-sheet-title">{title}</span>
          <span className="kebab-sheet-head-actions">
            {headerAction}
            <button type="button" className="kebab-sheet-x" onClick={onClose} aria-label="Close">
              <X size={18} strokeWidth={1.6} aria-hidden="true" />
            </button>
          </span>
        </div>
        <div className={'kebab-sheet-body' + (footer ? ' kebab-sheet-body--has-foot' : '')}>
          {children}
        </div>
        {footer && <div className="kebab-sheet-foot">{footer}</div>}
      </div>
    </>,
    document.body,
  )
}
