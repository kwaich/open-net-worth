// ArtifactFullscreen — the artifact's true edge-to-edge fullscreen view (owner, 2026-08-13:
// "Expand shouldn't show a modal... it should expand the entire artifact to full screen").
// Deliberately NOT the shared Modal/Radix Dialog — that renders a centered, padded, rounded,
// shadowed card over a dimmed backdrop, which is a fundamentally different shape than a true
// takeover. This is a plain fixed position:fixed; inset:0 overlay with no chrome of its own —
// ArtifactViewer supplies the entire header/body.
//
// Two entry points render this: LuciusHome's "Expand" from the side panel (passes onMinimize
// to return to the panel) and ArtifactCard's own "Open" on the dock/mobile, where there is no
// smaller panel state to minimize into (expandState omitted entirely there).
//
// On a phone it is a bottom sheet over a dimmed backdrop, dragged down to close like every
// other sheet (owner: "why is artifact not shown as a modal on mobile? with pull/drag to
// dismiss") — the flat takeover left the app's own header showing above it, reading as a page.

import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { useSheetDragToDismiss } from '../../../hooks/useSheetDragToDismiss'
import type { ArtifactRef } from '../../../types/chat'
import { ArtifactViewer, type ArtifactExpandState } from './ArtifactViewer'
import './artifact-fullscreen.css'

export function ArtifactFullscreen({
  artifact,
  onClose,
  onMinimize,
}: {
  artifact: ArtifactRef
  onClose: () => void
  /** Omit when there's no side panel to return to (dock/mobile) — Minimize doesn't render. */
  onMinimize?: () => void
}) {
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const isMobile = useIsMobile()
  const sheetRef = useRef<HTMLDivElement>(null)
  const scrimRef = useRef<HTMLDivElement>(null)
  const dragHandlers = useSheetDragToDismiss({
    sheetRef,
    overlayRef: scrimRef,
    onDismiss: onClose,
    enabled: isMobile,
  })

  const expandState: ArtifactExpandState | undefined = onMinimize
    ? { fullscreen: true, onToggle: onMinimize }
    : undefined

  return createPortal(
    // data-lucius-portal: the marker ui/Modal.tsx's isOutsideExempt() already looks for — a
    // click/focus/scroll inside this overlay must not read as "outside" some OTHER modal that
    // happens to be open at the same time (mirrors dragRightClick.tsx's own portaled content).
    <>
      {isMobile && (
        <div
          ref={scrimRef}
          className="artifact-fullscreen-scrim"
          data-lucius-portal
          onClick={onClose}
        />
      )}
      <div
        ref={sheetRef}
        className="artifact-fullscreen"
        role="dialog"
        aria-modal="true"
        data-lucius-portal
        {...(isMobile ? dragHandlers : {})}
      >
        {isMobile && (
          <div className="artifact-fullscreen-handle" aria-hidden="true">
            <span />
          </div>
        )}
        <ArtifactViewer artifact={artifact} onClose={onClose} expandState={expandState} />
      </div>
    </>,
    document.body,
  )
}
