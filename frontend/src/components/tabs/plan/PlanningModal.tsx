import * as Dialog from '@radix-ui/react-dialog'
import { useEffect, useRef, type CSSProperties, type ReactNode } from 'react'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { useSheetDragToDismiss } from '../../../hooks/useSheetDragToDismiss'

interface PlanningModalProps {
  onClose: () => void
  label: string
  children: ReactNode
  className?: string
  style?: CSSProperties
}

/**
 * Library-backed adapter for the Planning handoff's established pl-* chrome.
 * Planning owns its dense header/body/footer styling; Radix owns the modal
 * behavior, portal, focus lifecycle, inert background, stacking, and Escape.
 *
 * A structurally separate implementation from the shared `Modal` component (its own `.pl-*`
 * CSS family, not `.ui-modal-*`) — but it docks to the same mobile bottom-sheet geometry
 * (`.pl-ov`/`.pl-modal`'s own `@media (max-width: 640px)` block), so it gets the same
 * drag-to-dismiss gesture wired in directly here, same pattern as Modal.tsx.
 */
export function PlanningModal({ onClose, label, children, className, style }: PlanningModalProps) {
  const triggerRef = useRef<HTMLElement | null>(null)
  const contentRef = useRef<HTMLDivElement>(null)
  const overlayRef = useRef<HTMLDivElement>(null)
  const isMobile = useIsMobile()
  const dragHandlers = useSheetDragToDismiss({
    sheetRef: contentRef,
    overlayRef,
    onDismiss: onClose,
    enabled: isMobile,
  })

  useEffect(() => {
    if (document.activeElement instanceof HTMLElement) {
      triggerRef.current = document.activeElement
    }
  }, [])

  return (
    <Dialog.Root
      open
      onOpenChange={(next) => {
        if (!next) onClose()
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay ref={overlayRef} className="pl-ov">
          <Dialog.Content
            ref={contentRef}
            className={`pl-modal${className ? ` ${className}` : ''}`}
            style={style}
            aria-label={label}
            aria-describedby={undefined}
            {...dragHandlers}
            onEscapeKeyDown={(event) => {
              if (contentRef.current?.querySelector('[data-modal-escape-layer]')) {
                event.preventDefault()
              }
            }}
            onOpenAutoFocus={(event) => {
              // Radix's default is the first tabbable candidate in DOM order — for
              // categoriesManager that's the header's InfoPopover "i" button (it sits before the
              // close button), so opening Manage categories silently focused it and popped its
              // tooltip open with no interaction. Same opt-in convention as ui/Modal.tsx's
              // `[data-autofocus]`: only override when a consumer marked a preferred target.
              const preferred = contentRef.current?.querySelector<HTMLElement>('[data-autofocus]')
              if (!preferred) return
              event.preventDefault()
              preferred.focus({ preventScroll: true })
            }}
            onCloseAutoFocus={(event) => {
              const trigger = triggerRef.current
              if (!trigger || !document.contains(trigger)) return
              event.preventDefault()
              trigger.focus()
            }}
          >
            <Dialog.Title className="ui-visually-hidden">{label}</Dialog.Title>
            {/* Mobile-only visual affordance, matching Modal/.mb-sheet/.kebab-sheet's own
                handle bar — the whole card is the actual drag target either way. */}
            <div className="pl-modal-handle-wrap">
              <span className="pl-modal-handle" />
            </div>
            {children}
          </Dialog.Content>
        </Dialog.Overlay>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
