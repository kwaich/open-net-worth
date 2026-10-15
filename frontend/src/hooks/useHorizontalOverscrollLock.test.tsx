// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { useRef } from 'react'
import { useHorizontalOverscrollLock } from './useHorizontalOverscrollLock'

afterEach(() => cleanup())

function Harness({
  scrollLeft,
  scrollWidth,
  clientWidth,
}: {
  scrollLeft: number
  scrollWidth: number
  clientWidth: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  useHorizontalOverscrollLock(ref)
  return (
    <div
      ref={(el) => {
        ref.current = el
        if (!el) return
        Object.defineProperty(el, 'scrollLeft', { value: scrollLeft, configurable: true })
        Object.defineProperty(el, 'scrollWidth', { value: scrollWidth, configurable: true })
        Object.defineProperty(el, 'clientWidth', { value: clientWidth, configurable: true })
      }}
    />
  )
}

// jsdom doesn't implement TouchEvent's `touches` init dict, so a plain Event with `touches`
// bolted on stands in — the hook only ever reads `e.touches` and calls `e.preventDefault()`.
function touch(el: Element, type: string, clientX: number): boolean {
  const e = new Event(type, { bubbles: true, cancelable: true }) as unknown as TouchEvent
  Object.defineProperty(e, 'touches', { value: [{ clientX }] })
  return el.dispatchEvent(e)
}

describe('useHorizontalOverscrollLock', () => {
  it('cancels touchmove past the left edge (scrollLeft already 0, dragging further right)', () => {
    const { container } = render(<Harness scrollLeft={0} scrollWidth={1000} clientWidth={400} />)
    const el = container.firstChild as HTMLDivElement
    touch(el, 'touchstart', 100)
    const notCancelled = touch(el, 'touchmove', 130) // finger moves right -> would decrease scrollLeft below 0
    expect(notCancelled).toBe(false)
  })

  it('cancels touchmove past the right edge (scrollLeft already at max, dragging further left)', () => {
    const { container } = render(<Harness scrollLeft={600} scrollWidth={1000} clientWidth={400} />)
    const el = container.firstChild as HTMLDivElement
    touch(el, 'touchstart', 100)
    const notCancelled = touch(el, 'touchmove', 70) // finger moves left -> would push scrollLeft past max
    expect(notCancelled).toBe(false)
  })

  it('leaves an in-bounds touchmove alone (native momentum scroll keeps working)', () => {
    const { container } = render(<Harness scrollLeft={300} scrollWidth={1000} clientWidth={400} />)
    const el = container.firstChild as HTMLDivElement
    touch(el, 'touchstart', 100)
    const notCancelled = touch(el, 'touchmove', 70) // well within [0, 600], either direction is fine
    expect(notCancelled).toBe(true)
  })
})
