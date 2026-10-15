// @vitest-environment jsdom
//
// useSheetDragToDismiss's own DOM-level behaviour, exercised directly against a minimal harness
// rather than through any one pattern's full component — each pattern's own test file (Modal,
// TxnDetail, KebabMenu, SearchFilter) covers ITS wiring. `dragHandlers` are wired to the whole
// `sheetRef` element, exactly as every caller spreads them.
//
// The contract under test (see the hook's top-of-file note): content scrolls NATIVELY — the hook
// never writes `scrollTop` — and each gesture is decided ONCE at its first movement past the
// catch threshold: a drag when nothing under the finger can scroll, or the scrollable is at its
// top and the finger pulls down; otherwise it is the browser's for the rest of that gesture. The
// non-passive touchmove veto that keeps the browser from scrolling a touch the sheet owns is
// exercised by dispatching raw touch events, which is the only way it can be reached — React's
// own touch handlers are passive.
//
// Fake timers throughout: jsdom stamps `event.timeStamp` from `Date.now()`, so this is what
// makes release velocity deterministic instead of "two events in the same millisecond".

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { useRef } from 'react'
import { createPortal } from 'react-dom'
import {
  DRAG_IGNORE_PROPS,
  DRAG_SURFACE_PROPS,
  SCROLL_LOCK_TIMEOUT_MS,
  useSheetDragToDismiss,
} from './useSheetDragToDismiss'

function TestSheet({
  onDismiss,
  enabled,
  canStart,
}: {
  onDismiss: () => void
  enabled?: boolean
  canStart?: () => boolean
}) {
  const sheetRef = useRef<HTMLDivElement>(null)
  const dragHandlers = useSheetDragToDismiss({ sheetRef, onDismiss, enabled, canStart })
  return (
    <div ref={sheetRef} data-testid="sheet" {...dragHandlers}>
      <div data-testid="handle" />
      {/* TxnDetail's shape: chrome that lives inside the (root) scroller and opts in. */}
      <div data-testid="sticky-chrome" {...DRAG_SURFACE_PROPS}>
        <button data-testid="sticky-chrome-child">×</button>
      </div>
      {/* Scrollable overflow gets mocked per-test via scrollHeight/clientHeight — plain jsdom
       * elements report 0 for both, so `isScrollable` reads false unless a test opts in. */}
      <div data-testid="scrollable">scrollable body content</div>
      {/* The fullscreen chart's shape: a region that owns its own touch gestures (pan/zoom). */}
      <div data-testid="gesture-owner" {...DRAG_IGNORE_PROPS}>
        <canvas data-testid="gesture-owner-child" />
      </div>
    </div>
  )
}

interface Harness {
  onDismiss: ReturnType<typeof vi.fn>
  sheet: HTMLElement
  handle: HTMLElement
  scrollable: HTMLElement
  unmount: () => void
}

function mount(props: { enabled?: boolean; canStart?: () => boolean } = {}): Harness {
  const onDismiss = vi.fn()
  const { unmount } = render(<TestSheet onDismiss={onDismiss} {...props} />)
  const sheet = document.querySelector('[data-testid="sheet"]') as HTMLElement
  const handle = document.querySelector('[data-testid="handle"]') as HTMLElement
  const scrollable = document.querySelector('[data-testid="scrollable"]') as HTMLElement
  Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
  return { onDismiss, sheet, handle, scrollable, unmount }
}

/** 600px of content in a 300px viewport, parked at `scrollTop`. */
function makeScrollable(el: HTMLElement, scrollTop: number) {
  Object.defineProperty(el, 'scrollHeight', { value: 600, configurable: true })
  Object.defineProperty(el, 'clientHeight', { value: 300, configurable: true })
  el.style.overflowY = 'auto'
  el.scrollTop = scrollTop
}

/** Raw touch event with a plain `touches` array — jsdom has no Touch constructor, and the veto
 * only reads `touches.length` / `touches[0].clientX|clientY`. A bare number is a y on x = 0.
 * Returns whether it was cancelled. */
function touch(
  target: Element,
  type: 'touchstart' | 'touchmove',
  points: (number | { x: number; y: number })[],
  { cancelable = true } = {},
): boolean {
  const event = new Event(type, { bubbles: true, cancelable })
  const touches = points.map((p) =>
    typeof p === 'number' ? { clientX: 0, clientY: p } : { clientX: p.x, clientY: p.y },
  )
  Object.defineProperty(event, 'touches', { value: touches })
  target.dispatchEvent(event)
  return event.defaultPrevented
}

/** 800px-wide content in a 300px box that FITS vertically — the categories table on a small
 * book: it pans sideways, never up/down. */
function makeWideOnly(el: HTMLElement) {
  Object.defineProperty(el, 'scrollWidth', { value: 800, configurable: true })
  Object.defineProperty(el, 'clientWidth', { value: 300, configurable: true })
  el.style.overflowX = 'auto'
}

const settle = () => vi.advanceTimersByTime(300) // longer than either release animation

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useSheetDragToDismiss — catch threshold', () => {
  it('a move under CATCH_THRESHOLD_PX does not move the sheet (hand-tremor guard)', () => {
    const { sheet, handle } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 202, pointerId: 1 })
    expect(sheet.style.transform).toBe('')
  })

  it('a move past CATCH_THRESHOLD_PX catches and tracks the sheet 1:1', () => {
    const { sheet, handle } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 260, pointerId: 1 })
    expect(sheet.style.transform).toContain('translateY(60px)')
  })

  it('a pointer sequence starting on a non-scrollable descendant still drags the sheet', () => {
    const { sheet, scrollable } = mount()
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 1 })
    expect(sheet.style.transform).toContain('translateY(60px)')
  })

  it('a gesture starting inside a DRAG_IGNORE_PROPS region never moves the sheet', () => {
    // The fullscreen price chart docked as a phone sheet (owner ask 2026-09-21): a vertical
    // drag on the pane is the chart's own pan, a pinch is its zoom — the sheet must not
    // claim either, nor dismiss on them. Chrome around the chart (head, toolbar) still drags.
    const { sheet, onDismiss } = mount()
    const owner = document.querySelector('[data-testid="gesture-owner-child"]') as HTMLElement
    fireEvent.pointerDown(owner, { clientY: 100, pointerId: 1 })
    fireEvent.pointerMove(owner, { clientY: 400, pointerId: 1 })
    expect(sheet.style.transform).toBe('')
    fireEvent.pointerUp(owner, { clientY: 400, pointerId: 1 })
    settle()
    expect(onDismiss).not.toHaveBeenCalled()
    // …and the very next touch on ordinary chrome is not refused as "concurrent".
    const { handle } = { handle: document.querySelector('[data-testid="handle"]') as HTMLElement }
    fireEvent.pointerDown(handle, { clientY: 100, pointerId: 2 })
    fireEvent.pointerMove(handle, { clientY: 160, pointerId: 2 })
    expect(sheet.style.transform).toContain('translateY(60px)')
  })

  it('an upward pull on a non-scrollable sheet rubber-bands instead of tracking 1:1', () => {
    const { sheet, handle } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 100, pointerId: 1 })
    const px = Number(/translateY\((-?[\d.]+)px\)/.exec(sheet.style.transform)?.[1])
    expect(px).toBeLessThan(0)
    expect(px).toBeGreaterThan(-100)
  })
})

describe('useSheetDragToDismiss — release', () => {
  it('past the distance threshold dismisses, even at zero velocity', () => {
    const { sheet, handle, onDismiss } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 }) // 200px = 50% of 400
    expect(sheet.style.transform).toContain('translateY(200px)')
    fireEvent.pointerUp(handle, { clientY: 400, pointerId: 1 })
    settle()
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('a short but fast flick dismisses on velocity alone', () => {
    const { handle, onDismiss } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 210, pointerId: 1 })
    vi.advanceTimersByTime(20)
    fireEvent.pointerMove(handle, { clientY: 250, pointerId: 1 }) // 40px in 20ms = 2px/ms
    fireEvent.pointerUp(handle, { clientY: 250, pointerId: 1 }) // only 50px of 400 — distance alone would not
    settle()
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('under both thresholds springs back and clears every inline style it wrote', () => {
    const { sheet, handle, onDismiss } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 240, pointerId: 1 })
    vi.advanceTimersByTime(500) // a long hold: no velocity left in the window
    fireEvent.pointerUp(handle, { clientY: 240, pointerId: 1 })
    settle()
    expect(onDismiss).not.toHaveBeenCalled()
    expect(sheet.style.transform).toBe('')
    expect(sheet.style.transition).toBe('')
  })

  it('pointercancel never commits, however far the sheet was pulled', () => {
    const { sheet, handle, onDismiss } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 500, pointerId: 1 })
    fireEvent.pointerCancel(handle, { clientY: 500, pointerId: 1 })
    settle()
    expect(onDismiss).not.toHaveBeenCalled()
    expect(sheet.style.transform).toBe('')
  })

  it('a not-yet-laid-out sheet (offsetHeight 0) never hair-trigger commits', () => {
    const { sheet, handle, onDismiss } = mount()
    Object.defineProperty(sheet, 'offsetHeight', { value: 0, configurable: true })
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientY: 400, pointerId: 1 })
    settle()
    expect(onDismiss).not.toHaveBeenCalled()
  })
})

describe('useSheetDragToDismiss — drag or scroll, decided once per gesture', () => {
  it('over scrollable content at its top, a downward pull is a drag', () => {
    const { sheet, scrollable } = mount()
    makeScrollable(scrollable, 0)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 1 })
    expect(sheet.style.transform).toContain('translateY(60px)')
    expect(scrollable.scrollTop).toBe(0)
  })

  it('over scrolled content, a downward pull is the browser’s scroll — the sheet never moves and the hook never touches scrollTop', () => {
    const { sheet, scrollable, onDismiss } = mount()
    makeScrollable(scrollable, 150)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientY: 500, pointerId: 1 })
    fireEvent.pointerUp(scrollable, { clientY: 500, pointerId: 1 })
    settle()
    expect(sheet.style.transform).toBe('')
    expect(scrollable.scrollTop).toBe(150)
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('over scrollable content at its top, an UPWARD push is the browser’s scroll, not a rubber-band', () => {
    const { sheet, scrollable } = mount()
    makeScrollable(scrollable, 0)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientY: 100, pointerId: 1 })
    expect(sheet.style.transform).toBe('')
  })

  it('a gesture that started as a scroll stays a scroll even after the content reaches its top', () => {
    const { sheet, scrollable, onDismiss } = mount()
    makeScrollable(scrollable, 40)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 1 }) // decided: native
    scrollable.scrollTop = 0 // the browser scrolled it to the top under the same finger
    fireEvent.pointerMove(scrollable, { clientY: 500, pointerId: 1 }) // ...and the pull continues
    fireEvent.pointerUp(scrollable, { clientY: 500, pointerId: 1 })
    settle()
    expect(sheet.style.transform).toBe('')
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('the NEXT pull after scrolling to the top is a drag (the accepted contract)', () => {
    const { sheet, scrollable } = mount()
    makeScrollable(scrollable, 40)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 1 })
    scrollable.scrollTop = 0
    fireEvent.pointerUp(scrollable, { clientY: 260, pointerId: 1 })
    vi.advanceTimersByTime(SCROLL_LOCK_TIMEOUT_MS + 1)

    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 2 })
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 2 })
    expect(sheet.style.transform).toContain('translateY(60px)')
  })

  it('a pull within SCROLL_LOCK_TIMEOUT_MS of a declined gesture is still the browser’s (settling scroll guard)', () => {
    const { sheet, scrollable } = mount()
    makeScrollable(scrollable, 40)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 1 })
    scrollable.scrollTop = 0
    fireEvent.pointerUp(scrollable, { clientY: 260, pointerId: 1 })
    vi.advanceTimersByTime(SCROLL_LOCK_TIMEOUT_MS - 20)

    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 2 })
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 2 })
    expect(sheet.style.transform).toBe('')
  })

  it('the scroll lock never applies to non-scrolling chrome — the handle drags right after a declined scroll', () => {
    const { sheet, scrollable, handle } = mount()
    makeScrollable(scrollable, 40)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 1 })
    fireEvent.pointerUp(scrollable, { clientY: 260, pointerId: 1 })

    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 2 })
    fireEvent.pointerMove(handle, { clientY: 260, pointerId: 2 })
    expect(sheet.style.transform).toContain('translateY(60px)')
  })

  it('the sheet root itself may be the scroll container (TxnDetail) — at its top it drags, scrolled it does not', () => {
    const { sheet, scrollable } = mount()
    makeScrollable(sheet, 0)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 1 })
    expect(sheet.style.transform).toContain('translateY(60px)')
    fireEvent.pointerUp(scrollable, { clientY: 260, pointerId: 1 })
    settle()

    sheet.scrollTop = 80
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 2 })
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 2 })
    expect(sheet.style.transform).toBe('')
  })

  it('chrome inside the scroller that opts in (DRAG_SURFACE_PROPS) drags however far the root is scrolled — even from a child of it', () => {
    const { sheet, onDismiss } = mount()
    makeScrollable(sheet, 300)
    const chromeChild = document.querySelector('[data-testid="sticky-chrome-child"]') as HTMLElement
    fireEvent.pointerDown(chromeChild, { clientY: 100, pointerId: 1 })
    fireEvent.pointerMove(chromeChild, { clientY: 160, pointerId: 1 })
    expect(sheet.style.transform).toContain('translateY(60px)')
    expect(sheet.scrollTop).toBe(300)
    fireEvent.pointerMove(chromeChild, { clientY: 400, pointerId: 1 })
    fireEvent.pointerUp(chromeChild, { clientY: 400, pointerId: 1 })
    settle()
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('a sideways pan over scrollable content at its top is the browser’s (a horizontal scroll), and stays so when it later turns downward', () => {
    const { sheet, scrollable, onDismiss } = mount()
    makeScrollable(scrollable, 0)
    fireEvent.pointerDown(scrollable, { clientX: 100, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientX: 160, clientY: 210, pointerId: 1 })
    expect(sheet.style.transform).toBe('')
    fireEvent.pointerMove(scrollable, { clientX: 160, clientY: 500, pointerId: 1 })
    fireEvent.pointerUp(scrollable, { clientX: 160, clientY: 500, pointerId: 1 })
    settle()
    expect(sheet.style.transform).toBe('')
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('a region that only scrolls sideways counts as scrollable: a pan there is native, a downward pull still drags', () => {
    const { sheet, scrollable } = mount()
    makeWideOnly(scrollable)
    fireEvent.pointerDown(scrollable, { clientX: 100, clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(scrollable, { clientX: 40, clientY: 201, pointerId: 1 })
    expect(sheet.style.transform).toBe('')
    fireEvent.pointerUp(scrollable, { clientX: 40, clientY: 201, pointerId: 1 })
    vi.advanceTimersByTime(SCROLL_LOCK_TIMEOUT_MS + 1)

    fireEvent.pointerDown(scrollable, { clientX: 100, clientY: 200, pointerId: 2 })
    fireEvent.pointerMove(scrollable, { clientX: 102, clientY: 260, pointerId: 2 })
    expect(sheet.style.transform).toContain('translateY(60px)')
  })
})

describe('useSheetDragToDismiss — vetoes', () => {
  it('canStart() === false leaves the gesture to the browser', () => {
    const { sheet, handle, onDismiss } = mount({ canStart: () => false })
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientY: 400, pointerId: 1 })
    settle()
    expect(sheet.style.transform).toBe('')
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('enabled === false does nothing at all, not even bind the touch veto', () => {
    const { sheet, handle } = mount({ enabled: false })
    const spy = vi.spyOn(sheet, 'addEventListener')
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 })
    expect(sheet.style.transform).toBe('')
    expect(spy).not.toHaveBeenCalledWith('touchmove', expect.anything(), expect.anything())
  })
})

describe('useSheetDragToDismiss — a surface portaled out of the sheet', () => {
  // A Modal opened FROM a sheet (the transaction drawer's receipt viewer) lives on <body>, yet
  // stays a React child of the sheet — and React events bubble through portals. The drawer took
  // the pointer capture from the viewer and slid down itself (owner report 2026-09-26).
  function SheetWithPortal({ onDismiss }: { onDismiss: () => void }) {
    const sheetRef = useRef<HTMLDivElement>(null)
    const dragHandlers = useSheetDragToDismiss({ sheetRef, onDismiss })
    return (
      <div ref={sheetRef} data-testid="sheet" {...dragHandlers}>
        <div data-testid="own-content" />
        {createPortal(<div data-testid="portaled" />, document.body)}
      </div>
    )
  }

  function mountWithPortal() {
    const onDismiss = vi.fn()
    render(<SheetWithPortal onDismiss={onDismiss} />)
    const sheet = document.querySelector('[data-testid="sheet"]') as HTMLElement
    Object.defineProperty(sheet, 'offsetHeight', { value: 400, configurable: true })
    return {
      onDismiss,
      sheet,
      own: document.querySelector('[data-testid="own-content"]') as HTMLElement,
      portaled: document.querySelector('[data-testid="portaled"]') as HTMLElement,
    }
  }

  it('a drag on it neither moves nor dismisses the sheet', () => {
    const { sheet, portaled, onDismiss } = mountWithPortal()
    fireEvent.pointerDown(portaled, { clientY: 100, pointerId: 1 })
    fireEvent.pointerMove(portaled, { clientY: 400, pointerId: 1 })
    expect(sheet.style.transform).toBe('')
    fireEvent.pointerUp(portaled, { clientY: 400, pointerId: 1 })
    settle()
    expect(onDismiss).not.toHaveBeenCalled()
  })

  it('the sheet still drags from its own content', () => {
    const { sheet, own, portaled } = mountWithPortal()
    fireEvent.pointerDown(portaled, { clientY: 100, pointerId: 1 })
    fireEvent.pointerUp(portaled, { clientY: 100, pointerId: 1 })
    fireEvent.pointerDown(own, { clientY: 200, pointerId: 2 })
    fireEvent.pointerMove(own, { clientY: 260, pointerId: 2 })
    expect(sheet.style.transform).toContain('translateY(60px)')
  })
})

describe('useSheetDragToDismiss — a pointer whose release never arrived', () => {
  it('does not block the next touch: a pointerdown that was never released is let go of', () => {
    // A pointerup can go missing for real — a descendant hook claims the gesture, iOS's own
    // edge gesture swallows the release — and every touch is a NEW pointerId. Holding on to an
    // undecided pointer forever would refuse every later touch as "a second concurrent
    // pointer" and the sheet could never be dragged again until remount.
    const { sheet, handle, onDismiss } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    // …no move, no pointerup, no pointercancel for pointer 1, ever.

    fireEvent.pointerDown(handle, { clientY: 100, pointerId: 2 })
    fireEvent.pointerMove(handle, { clientY: 300, pointerId: 2 })
    expect(sheet.style.transform).toContain('translateY(200px)')
    fireEvent.pointerUp(handle, { clientY: 300, pointerId: 2 })
    settle()
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})

describe('useSheetDragToDismiss — a second concurrent pointer (multi-touch)', () => {
  it('never orphans the first pointer drag — its own pointerup still settles cleanly', () => {
    const { sheet, handle, onDismiss } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 })
    expect(sheet.style.transform).toContain('translateY(200px)')

    // A second finger touches down mid-gesture and lifts again. Neither event may overwrite
    // or clear pointer 1's tracked state.
    fireEvent.pointerDown(handle, { clientY: 400, pointerId: 2 })
    fireEvent.pointerUp(handle, { clientY: 400, pointerId: 2 })

    fireEvent.pointerMove(handle, { clientY: 600, pointerId: 1 })
    expect(sheet.style.transform).toContain('translateY(400px)')
    fireEvent.pointerUp(handle, { clientY: 600, pointerId: 1 })
    settle()
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })
})

describe('useSheetDragToDismiss — the non-passive touchmove veto', () => {
  it('cancels a downward touchmove over scrollable content at its top (the sheet will own it)', () => {
    const { scrollable } = mount()
    makeScrollable(scrollable, 0)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    touch(scrollable, 'touchstart', [200])
    expect(touch(scrollable, 'touchmove', [230])).toBe(true)
  })

  it('leaves a touchmove alone over scrolled content in either direction (the browser scrolls)', () => {
    const { scrollable } = mount()
    makeScrollable(scrollable, 150)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    touch(scrollable, 'touchstart', [200])
    expect(touch(scrollable, 'touchmove', [230])).toBe(false)
    expect(touch(scrollable, 'touchmove', [180])).toBe(false)
  })

  it('cancels an upward touchmove at the bottom of the content (no elastic bounce to cancel our pointer stream)', () => {
    const { scrollable } = mount()
    makeScrollable(scrollable, 300)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    touch(scrollable, 'touchstart', [200])
    expect(touch(scrollable, 'touchmove', [170])).toBe(true)
  })

  it('leaves a sideways pan alone over scrollable content at its top, vertical jitter and all (the categories table scrolls horizontally)', () => {
    const { scrollable } = mount()
    makeScrollable(scrollable, 0)
    fireEvent.pointerDown(scrollable, { clientX: 100, clientY: 200, pointerId: 1 })
    touch(scrollable, 'touchstart', [{ x: 100, y: 200 }])
    expect(touch(scrollable, 'touchmove', [{ x: 120, y: 202 }])).toBe(false)
    expect(touch(scrollable, 'touchmove', [{ x: 150, y: 199 }])).toBe(false)
    // ...whereas the same pull straight down from rest is the sheet's.
    touch(scrollable, 'touchstart', [{ x: 100, y: 200 }])
    expect(touch(scrollable, 'touchmove', [{ x: 102, y: 230 }])).toBe(true)
  })

  it('leaves a sideways pan alone over a region that only scrolls sideways', () => {
    const { scrollable } = mount()
    makeWideOnly(scrollable)
    fireEvent.pointerDown(scrollable, { clientX: 100, clientY: 200, pointerId: 1 })
    touch(scrollable, 'touchstart', [{ x: 100, y: 200 }])
    expect(touch(scrollable, 'touchmove', [{ x: 60, y: 201 }])).toBe(false)
  })

  it('cancels every touchmove over non-scrolling chrome, sideways included', () => {
    const { handle } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    touch(handle, 'touchstart', [200])
    expect(touch(handle, 'touchmove', [230])).toBe(true)
    expect(touch(handle, 'touchmove', [200])).toBe(true)
    expect(touch(handle, 'touchmove', [{ x: 60, y: 201 }])).toBe(true)
  })

  it('cancels every touchmove over opted-in chrome inside a scrolled root — the root must not scroll under a handle drag', () => {
    const { sheet } = mount()
    makeScrollable(sheet, 300)
    const chrome = document.querySelector('[data-testid="sticky-chrome"]') as HTMLElement
    fireEvent.pointerDown(chrome, { clientY: 200, pointerId: 1 })
    touch(chrome, 'touchstart', [200])
    expect(touch(chrome, 'touchmove', [230])).toBe(true)
    expect(touch(chrome, 'touchmove', [200])).toBe(true)
  })

  it('cancels a reversal mid-drag that the boundary rule alone would let scroll', () => {
    const { sheet, scrollable } = mount()
    makeScrollable(scrollable, 0)
    fireEvent.pointerDown(scrollable, { clientY: 200, pointerId: 1 })
    touch(scrollable, 'touchstart', [200])
    fireEvent.pointerMove(scrollable, { clientY: 260, pointerId: 1 })
    expect(sheet.style.transform).toContain('translateY(60px)')
    // Finger comes back up: content is at top, not bottom — nothing at a boundary in that
    // direction, but the sheet is mid-drag, so the browser must still not start a scroll.
    expect(touch(scrollable, 'touchmove', [240])).toBe(true)
  })

  it('never cancels a multi-touch gesture, even after one finger lifts', () => {
    const { handle } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    touch(handle, 'touchstart', [200, 210])
    expect(touch(handle, 'touchmove', [230, 240])).toBe(false)
    expect(touch(handle, 'touchmove', [260])).toBe(false)
  })

  it('never calls preventDefault on a non-cancelable touchmove', () => {
    const { handle } = mount()
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    touch(handle, 'touchstart', [200])
    expect(touch(handle, 'touchmove', [230], { cancelable: false })).toBe(false)
  })

  it('is bound once on the element the handlers live on, and unbound on unmount', () => {
    const { sheet, handle, unmount } = mount()
    const add = vi.spyOn(sheet, 'addEventListener')
    const remove = vi.spyOn(sheet, 'removeEventListener')
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerUp(handle, { clientY: 200, pointerId: 1 })
    fireEvent.pointerDown(handle, { clientY: 200, pointerId: 2 })
    const touchmoveBinds = add.mock.calls.filter(([type]) => type === 'touchmove')
    expect(touchmoveBinds).toHaveLength(1)
    expect(touchmoveBinds[0]?.[2]).toEqual({ passive: false })
    unmount()
    expect(remove).toHaveBeenCalledWith('touchmove', expect.any(Function))
  })
})
