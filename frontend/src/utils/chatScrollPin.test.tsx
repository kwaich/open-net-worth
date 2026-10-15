// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useRef } from 'react'
import { newScrollIntent, useChatScrollPin } from './chatScroll'

// ---------------------------------------------------------------------------
// Minimal ResizeObserver stub — lets tests trigger the callback manually.
// ---------------------------------------------------------------------------

class MockResizeObserver {
  static instances: MockResizeObserver[] = []
  callback: ResizeObserverCallback
  targets: Element[] = []
  disconnected = false

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback
    MockResizeObserver.instances.push(this)
  }
  observe(el: Element) {
    this.targets.push(el)
  }
  unobserve(el: Element) {
    this.targets = this.targets.filter((t) => t !== el)
  }
  disconnect() {
    this.disconnected = true
  }

  /** Fire the callback only when the element was actually observed. */
  trigger(target: Element | undefined = this.targets[0]) {
    if (!target || !this.targets.includes(target)) return
    this.callback([{ target } as ResizeObserverEntry], this)
  }
}

/** The single observer the hook is expected to have created. */
function observer(): MockResizeObserver {
  const inst = MockResizeObserver.instances[0]
  if (!inst) throw new Error('expected a MockResizeObserver instance')
  return inst
}

describe('useChatScrollPin', () => {
  const origRO = global.ResizeObserver

  beforeEach(() => {
    MockResizeObserver.instances = []
    global.ResizeObserver = MockResizeObserver
  })

  afterEach(() => {
    global.ResizeObserver = origRO
    vi.restoreAllMocks()
  })

  function makeScrollContainer(scrollHeight: number) {
    // A REAL DOM node: the hook also attaches a MutationObserver, and jsdom's
    // MutationObserver.observe rejects anything that is not a Node.
    const el = document.createElement('div')
    Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true })
    Object.defineProperty(el, 'clientHeight', { value: 100, configurable: true })
    el.append(document.createElement('div'))
    return el
  }

  it('creates a ResizeObserver and observes the container', () => {
    const el = makeScrollContainer(500)
    const { result } = renderHook(() => {
      const containerRef = useRef<HTMLDivElement>(el)
      const isNearBottomRef = useRef(true)
      useChatScrollPin(containerRef, isNearBottomRef)
      return { containerRef, isNearBottomRef }
    })
    expect(MockResizeObserver.instances).toHaveLength(1)
    expect(observer().targets).toContain(result.current.containerRef.current)
  })

  it('snaps scrollTop to scrollHeight when content grows and user is near bottom', () => {
    const el = makeScrollContainer(500)
    renderHook(() => {
      const containerRef = useRef<HTMLDivElement>(el)
      const isNearBottomRef = useRef(true)
      useChatScrollPin(containerRef, isNearBottomRef)
    })

    // Simulate content growth: scrollHeight increases
    Object.defineProperty(el, 'scrollHeight', { value: 700, configurable: true })
    observer().trigger()

    expect(el.scrollTop).toBe(700)
  })

  it('does NOT scroll when user has scrolled up (isNearBottom false)', () => {
    const el = makeScrollContainer(500)
    renderHook(() => {
      const containerRef = useRef<HTMLDivElement>(el)
      const isNearBottomRef = useRef(false) // user scrolled up
      useChatScrollPin(containerRef, isNearBottomRef)
    })

    Object.defineProperty(el, 'scrollHeight', { value: 700, configurable: true })
    observer().trigger()

    // scrollTop should remain unchanged
    expect(el.scrollTop).toBe(0)
  })

  it('does NOT scroll when scrollHeight did not increase (no content growth)', () => {
    const el = makeScrollContainer(500)
    renderHook(() => {
      const containerRef = useRef<HTMLDivElement>(el)
      const isNearBottomRef = useRef(true)
      useChatScrollPin(containerRef, isNearBottomRef)
    })

    // scrollHeight stays the same — e.g. a style recalc without new content
    observer().trigger()

    expect(el.scrollTop).toBe(0)
  })

  it('disconnects the observer on unmount', () => {
    const el = makeScrollContainer(500)
    const { unmount } = renderHook(() => {
      const containerRef = useRef<HTMLDivElement>(el)
      const isNearBottomRef = useRef(true)
      useChatScrollPin(containerRef, isNearBottomRef)
    })

    expect(observer().disconnected).toBe(false)
    unmount()
    expect(observer().disconnected).toBe(true)
  })

  it('tracks incremental growth across multiple resize events', () => {
    const el = makeScrollContainer(500)
    renderHook(() => {
      const containerRef = useRef<HTMLDivElement>(el)
      const isNearBottomRef = useRef(true)
      useChatScrollPin(containerRef, isNearBottomRef)
    })

    // First growth
    Object.defineProperty(el, 'scrollHeight', { value: 600, configurable: true })
    observer().trigger()
    expect(el.scrollTop).toBe(600)

    // Second growth — lastScrollHeight should now be 600, so 700 > 600 triggers
    Object.defineProperty(el, 'scrollHeight', { value: 700, configurable: true })
    observer().trigger()
    expect(el.scrollTop).toBe(700)
  })

  it('re-pins when an already-rendered transcript settles to a taller height', () => {
    const el = makeScrollContainer(500)
    const transcript = el.firstElementChild as HTMLElement
    renderHook(() => {
      const containerRef = useRef<HTMLDivElement>(el)
      const isNearBottomRef = useRef(true)
      useChatScrollPin(containerRef, isNearBottomRef)
    })

    // Existing markdown, image and content-visibility nodes can finish layout without adding
    // another DOM child. The scroll container's own box stays fixed; only its transcript grows.
    Object.defineProperty(el, 'scrollHeight', { value: 900, configurable: true })
    observer().trigger(transcript)

    expect(el.scrollTop).toBe(900)
  })

  it('does nothing when containerRef.current is null (panel not yet mounted)', () => {
    // Should not throw and should create no observer
    expect(() => {
      renderHook(() => {
        const containerRef = useRef<HTMLDivElement>(null)
        const isNearBottomRef = useRef(true)
        useChatScrollPin(containerRef, isNearBottomRef)
      })
    }).not.toThrow()
    // No observer created when el is null
    expect(MockResizeObserver.instances).toHaveLength(0)
  })

  // ── Expanding a row is the READER acting, not new content arriving ──────────
  //
  // Expand a tool row while near the bottom and the MutationObserver saw the detail panel
  // appear, called it growth, and snapped to the bottom — so the row you just opened was
  // now above the viewport and you had to scroll up to find it (owner report, 2026-08-25).

  function pin(el: HTMLElement, nearBottom = true) {
    renderHook(() => {
      const containerRef = useRef<HTMLDivElement>(el as HTMLDivElement)
      const isNearBottomRef = useRef(nearBottom)
      useChatScrollPin(containerRef, isNearBottomRef)
    })
  }

  function grow(el: HTMLElement, to: number) {
    Object.defineProperty(el, 'scrollHeight', { value: to, configurable: true })
    observer().trigger()
  }

  it('does NOT snap for growth the reader caused by clicking', () => {
    const el = makeScrollContainer(500)
    pin(el)
    el.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    grow(el, 700)
    expect(el.scrollTop).toBe(0)
  })

  it('does NOT snap for growth the reader caused with the keyboard', () => {
    // A row opened with Enter is the same act as one opened with a mouse.
    const el = makeScrollContainer(500)
    pin(el)
    el.dispatchEvent(new Event('keydown', { bubbles: true }))
    grow(el, 700)
    expect(el.scrollTop).toBe(0)
  })

  it('still snaps for a real message arriving after the reader has settled', () => {
    const el = makeScrollContainer(500)
    pin(el)
    el.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    grow(el, 700) // the expand — ignored

    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 5_000)
    grow(el, 900) // a reply lands
    expect(el.scrollTop).toBe(900)
  })

  // Persona QA 2026-10-14: a long reply could not be read from its top while it was still
  // streaming. A gentle scroll up stays inside the 80px "near the bottom" band, and the very
  // next token re-pinned the view before the scroll event had a chance to say so.
  describe('a reader scrolling up while a reply streams', () => {
    function pinWithRef(el: HTMLElement) {
      const hold: { near?: { current: boolean } } = {}
      renderHook(() => {
        const containerRef = useRef<HTMLDivElement>(el as HTMLDivElement)
        const isNearBottomRef = useRef(true)
        const intent = useRef(newScrollIntent())
        hold.near = isNearBottomRef
        useChatScrollPin(containerRef, isNearBottomRef, true, intent)
      })
      return hold.near!
    }

    it('stops following on the first wheel notch up, before the next token lands', () => {
      const el = makeScrollContainer(500)
      const near = pinWithRef(el)
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: -20, bubbles: true }))
      expect(near.current).toBe(false)
      grow(el, 560)
      expect(el.scrollTop).toBe(0) // not yanked back down
    })

    it('keeps following when the wheel moves down', () => {
      const el = makeScrollContainer(500)
      const near = pinWithRef(el)
      el.dispatchEvent(new WheelEvent('wheel', { deltaY: 40, bubbles: true }))
      expect(near.current).toBe(true)
      grow(el, 560)
      expect(el.scrollTop).toBe(560)
    })

    it.each(['PageUp', 'ArrowUp', 'Home'])('stops following on %s', (key) => {
      const el = makeScrollContainer(500)
      const near = pinWithRef(el)
      el.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }))
      expect(near.current).toBe(false)
    })

    it('stops following when a finger drags the content up the screen', () => {
      const el = makeScrollContainer(500)
      const near = pinWithRef(el)
      const touch = (y: number) => [{ clientY: y }] as unknown as Touch[]
      const fire = (type: string, y: number) => {
        const e = new Event(type, { bubbles: true }) as Event & { touches: Touch[] }
        e.touches = touch(y)
        el.dispatchEvent(e)
      }
      fire('touchstart', 100)
      fire('touchmove', 90) // finger up: reading further down, still following
      expect(near.current).toBe(true)
      fire('touchmove', 140) // finger down: scrolling up
      expect(near.current).toBe(false)
    })
  })

  it('measures the next growth against the EXPANDED height, not the stale one', () => {
    // The suppressed tick must still move the baseline, or the very next observer tick
    // compares 700 against 500, calls it growth, and snaps anyway.
    const el = makeScrollContainer(500)
    pin(el)
    el.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    grow(el, 700)

    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 5_000)
    observer().trigger() // same height, no new content
    expect(el.scrollTop).toBe(0)
  })
})
