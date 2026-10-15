// @vitest-environment jsdom
//
// useSwipeBack's own DOM-level behaviour: a rightward, horizontal-dominant pull past the
// commit distance (or a fast rightward flick) calls `onBack` — DURING the gesture, not on
// release, so it never depends on actually receiving a pointerup/pointercancel. Anything else —
// a vertical drag (a normal scroll), a leftward swipe, or a rightward pull that never reaches
// the threshold — must leave the gesture to the browser/content and never call it. Settings.tsx
// wires the real caller (the drilled-in section pane); this exercises the hook directly against
// a minimal harness, the same pattern useSheetDragToDismiss.test.tsx uses for its own hook.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import { useRef } from 'react'
import { useSwipeBack } from './useSwipeBack'
import { useSheetDragToDismiss } from './useSheetDragToDismiss'

function TestPane({ onBack, enabled }: { onBack: () => void; enabled?: boolean }) {
  const handlers = useSwipeBack(onBack, enabled)
  return <div data-testid="pane" {...handlers} />
}

/** Simulates Settings' real DOM shape: the pane's handlers sit on a DESCENDANT of Modal's own
 * whole-card drag-to-dismiss surface, so every pointer event bubbles up to it too. */
function TestPaneInsideCard({
  onBack,
  onCardPointerMove,
}: {
  onBack: () => void
  onCardPointerMove: (event: ReactPointerEvent) => void
}) {
  const handlers = useSwipeBack(onBack, true)
  return (
    <div data-testid="card" onPointerMove={onCardPointerMove}>
      <div data-testid="pane" {...handlers} />
    </div>
  )
}

/** The two REAL hooks in Settings' exact arrangement: Modal's whole-card drag-to-dismiss on
 * the card, swipe-back on the drilled-in pane inside it. */
function TestPaneInsideRealSheet({
  onBack,
  onDismiss,
}: {
  onBack: () => void
  onDismiss: () => void
}) {
  const sheetRef = useRef<HTMLDivElement>(null)
  const dragHandlers = useSheetDragToDismiss({ sheetRef, onDismiss })
  const swipeHandlers = useSwipeBack(onBack, true)
  return (
    <div ref={sheetRef} data-testid="card" {...dragHandlers}>
      <div data-testid="pane" {...swipeHandlers} />
    </div>
  )
}

function mount(props: { enabled?: boolean } = {}) {
  const onBack = vi.fn()
  const { unmount } = render(<TestPane onBack={onBack} {...props} />)
  const pane = document.querySelector('[data-testid="pane"]') as HTMLElement
  return { onBack, pane, unmount }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useSwipeBack', () => {
  it('a rightward swipe past the commit distance calls onBack — mid-gesture, before release', () => {
    const { onBack, pane } = mount()
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 140, clientY: 200, pointerId: 1 }) // 120px right
    // No pointerUp fired at all — onBack must already have been called from the move alone.
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('fires even if pointerup/pointercancel never arrives (the iOS edge-gesture hazard)', () => {
    const { onBack, pane } = mount()
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 140, clientY: 200, pointerId: 1 })
    // Simulate the OS taking over the touch stream entirely: no release event of any kind.
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('a fast rightward flick commits even under the distance threshold', () => {
    const { onBack, pane } = mount()
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 30, clientY: 200, pointerId: 1 })
    vi.advanceTimersByTime(20)
    fireEvent.pointerMove(pane, { clientX: 70, clientY: 200, pointerId: 1 }) // 40px in 20ms = 2px/ms
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('a rightward swipe under the commit distance never calls onBack', () => {
    const { onBack, pane } = mount()
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 60, clientY: 200, pointerId: 1 }) // 40px, below 80px
    fireEvent.pointerUp(pane, { clientX: 60, clientY: 200, pointerId: 1 })
    expect(onBack).not.toHaveBeenCalled()
  })

  it('a leftward swipe never calls onBack', () => {
    const { onBack, pane } = mount()
    fireEvent.pointerDown(pane, { clientX: 140, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerUp(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    expect(onBack).not.toHaveBeenCalled()
  })

  it('a vertical-dominant drag (a normal scroll) is decided as native and never calls onBack', () => {
    const { onBack, pane } = mount()
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    // First real movement is mostly vertical — decided 'native' for the rest of the gesture.
    fireEvent.pointerMove(pane, { clientX: 22, clientY: 260, pointerId: 1 })
    // Even a later, large rightward pull cannot flip a gesture already decided native.
    fireEvent.pointerMove(pane, { clientX: 200, clientY: 260, pointerId: 1 })
    expect(onBack).not.toHaveBeenCalled()
  })

  it('does nothing at all while disabled', () => {
    const { onBack, pane } = mount({ enabled: false })
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 140, clientY: 200, pointerId: 1 })
    expect(onBack).not.toHaveBeenCalled()
  })

  it('fires only once per gesture, even with further rightward movement after commit', () => {
    const { onBack, pane } = mount()
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 140, clientY: 200, pointerId: 1 }) // commits
    fireEvent.pointerMove(pane, { clientX: 200, clientY: 200, pointerId: 1 }) // still moving
    fireEvent.pointerUp(pane, { clientX: 200, clientY: 200, pointerId: 1 })
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('a fresh gesture after a completed one can fire onBack again', () => {
    const { onBack, pane } = mount()
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 140, clientY: 200, pointerId: 1 })
    fireEvent.pointerUp(pane, { clientX: 140, clientY: 200, pointerId: 1 })
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 2 })
    fireEvent.pointerMove(pane, { clientX: 140, clientY: 200, pointerId: 2 })
    expect(onBack).toHaveBeenCalledTimes(2)
  })

  it('once decided as a swipe, stops the gesture reaching an ancestor drag handler', () => {
    // Settings' real DOM shape: these handlers sit on a descendant of Modal's own whole-card
    // vertical drag-to-dismiss surface — every pointer event bubbles up to it too, unless this
    // hook claims the gesture. Reproduces the "swiping right also pulls the sheet down" report.
    const onBack = vi.fn()
    const onCardPointerMove = vi.fn()
    render(<TestPaneInsideCard onBack={onBack} onCardPointerMove={onCardPointerMove} />)
    const pane = document.querySelector('[data-testid="pane"]') as HTMLElement
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    // The very first move that decides the gesture is ALSO the first one stopped — the card
    // never sees any part of an owned swipe, not even the deciding event.
    fireEvent.pointerMove(pane, { clientX: 30, clientY: 202, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 60, clientY: 203, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 140, clientY: 204, pointerId: 1 })
    expect(onCardPointerMove).not.toHaveBeenCalled()
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it("a vertical scroll gesture's events still reach the ancestor drag handler normally", () => {
    const onBack = vi.fn()
    const onCardPointerMove = vi.fn()
    render(<TestPaneInsideCard onBack={onBack} onCardPointerMove={onCardPointerMove} />)
    const pane = document.querySelector('[data-testid="pane"]') as HTMLElement
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 22, clientY: 260, pointerId: 1 }) // vertical-dominant
    fireEvent.pointerMove(pane, { clientX: 24, clientY: 320, pointerId: 1 })
    expect(onCardPointerMove).toHaveBeenCalledTimes(2)
    expect(onBack).not.toHaveBeenCalled()
  })

  it('TRAILING moves after commit — while the finger is still down — never reach the card either', () => {
    // The actual bug: onBack fires mid-gesture (by design), but the finger is still physically
    // on the glass generating more pointermoves right up to lift-off. Those trailing events
    // must stay claimed too, or they leak straight through to the card as if this hook had
    // already finished — which is exactly what pulled the sheet down on a real device (a fast
    // swipe commits on an early move, then several more moves arrive before pointerup).
    const onBack = vi.fn()
    const onCardPointerMove = vi.fn()
    render(<TestPaneInsideCard onBack={onBack} onCardPointerMove={onCardPointerMove} />)
    const pane = document.querySelector('[data-testid="pane"]') as HTMLElement
    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 200, clientY: 200, pointerId: 1 }) // 180px — commits immediately
    expect(onBack).toHaveBeenCalledTimes(1)
    // Finger keeps moving (with real-finger vertical wobble) before lifting off.
    fireEvent.pointerMove(pane, { clientX: 210, clientY: 204, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 220, clientY: 208, pointerId: 1 })
    fireEvent.pointerUp(pane, { clientX: 220, clientY: 208, pointerId: 1 })
    expect(onCardPointerMove).not.toHaveBeenCalled()
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('after a swipe-back, the NEXT touch can still drag the card to dismiss (owner report 2026-09-05)', () => {
    // "After going back from a sub tab to the main settings, I can't drag to dismiss anymore."
    // The swipe-back hides an owned gesture's moves AND its release from the card's hook — but
    // the card's hook already saw the pointerdown and is tracking that pointer. Hiding the
    // release left it tracking a pointer that would never end; on a touchscreen every contact is
    // a NEW pointerId, so every later touch was refused as "a second concurrent pointer".
    const onBack = vi.fn()
    const onDismiss = vi.fn()
    render(<TestPaneInsideRealSheet onBack={onBack} onDismiss={onDismiss} />)
    const card = document.querySelector('[data-testid="card"]') as HTMLElement
    const pane = document.querySelector('[data-testid="pane"]') as HTMLElement
    Object.defineProperty(card, 'offsetHeight', { value: 400, configurable: true })

    fireEvent.pointerDown(pane, { clientX: 20, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(pane, { clientX: 140, clientY: 202, pointerId: 1 })
    fireEvent.pointerUp(pane, { clientX: 140, clientY: 202, pointerId: 1 })
    expect(onBack).toHaveBeenCalledTimes(1)
    expect(card.style.transform).toBe('')

    // Back on the section list now (the pane is gone in the real app; here it simply isn't
    // touched). A fresh finger pulls the card down — a new pointerId, as on a real touchscreen.
    fireEvent.pointerDown(card, { clientX: 100, clientY: 100, pointerId: 2 })
    fireEvent.pointerMove(card, { clientX: 100, clientY: 300, pointerId: 2 })
    expect(card.style.transform).toContain('translateY(200px)')
    fireEvent.pointerUp(card, { clientX: 100, clientY: 300, pointerId: 2 })
    vi.advanceTimersByTime(300)
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})
