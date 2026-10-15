// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { useRef } from 'react'
import { useEdgeSwipeBack } from './useEdgeSwipeBack'

function Page({ onBack, enabled = true }: { onBack: () => void; enabled?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useEdgeSwipeBack(ref, onBack, enabled)
  return <div ref={ref} data-testid="page" />
}

function touch(el: Element, type: string, x: number, y: number, t: number) {
  const ev = new Event(type, { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'touches', {
    value: type === 'touchend' ? [] : [{ clientX: x, clientY: y }],
  })
  Object.defineProperty(ev, 'timeStamp', { value: t })
  el.dispatchEvent(ev)
  return ev
}

function swipe(el: Element, from: [number, number], to: [number, number], ms: number) {
  touch(el, 'touchstart', from[0], from[1], 0)
  touch(el, 'touchmove', (from[0] + to[0]) / 2, (from[1] + to[1]) / 2, ms / 2)
  const move = touch(el, 'touchmove', to[0], to[1], ms)
  touch(el, 'touchend', to[0], to[1], ms)
  return move
}

function mount(onBack: () => void, enabled = true) {
  render(<Page onBack={onBack} enabled={enabled} />)
  const el = screen.getByTestId('page')
  el.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 800 }) as DOMRect
  return el
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.useRealTimers()
  cleanup()
})

describe('useEdgeSwipeBack', () => {
  it('a swipe from the left edge past a third of the width goes back', () => {
    const onBack = vi.fn()
    const el = mount(onBack)
    const move = swipe(el, [8, 300], [220, 310], 600)
    expect(move.defaultPrevented).toBe(true) // it owns the gesture: no scroll underneath
    expect(el.style.transform).toBe('translateX(400px)')
    vi.advanceTimersByTime(200)
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('goes back with the page still off-screen — never painted back in place first', () => {
    let atBack = ''
    const el = mount(() => {
      atBack = el.style.transform
    })
    swipe(el, [8, 300], [260, 300], 500)
    vi.advanceTimersByTime(200)
    expect(atBack).toBe('translateX(400px)')
  })

  it('a quick flick goes back too; a short slow pull springs back', () => {
    const onBack = vi.fn()
    const el = mount(onBack)
    swipe(el, [8, 300], [80, 300], 400) // 72px, slow
    vi.advanceTimersByTime(200)
    expect(onBack).not.toHaveBeenCalled()
    expect(el.style.transform).toBe('')
    swipe(el, [8, 300], [90, 300], 60) // fast flick
    vi.advanceTimersByTime(200)
    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it('ignores a swipe that starts away from the edge, a vertical scroll, and a disabled view', () => {
    const onBack = vi.fn()
    const el = mount(onBack)
    swipe(el, [120, 300], [380, 300], 300)
    swipe(el, [8, 300], [20, 560], 300)
    vi.advanceTimersByTime(200)
    expect(onBack).not.toHaveBeenCalled()
    cleanup()
    const off = mount(onBack, false)
    swipe(off, [8, 300], [300, 300], 300)
    vi.advanceTimersByTime(200)
    expect(onBack).not.toHaveBeenCalled()
  })

  it('an edge touch never reaches the sheet underneath (its drag-to-dismiss)', () => {
    const el = mount(vi.fn())
    const sheet = vi.fn()
    el.parentElement?.addEventListener('pointerdown', sheet)
    const at = (x: number) =>
      el.dispatchEvent(
        Object.assign(new Event('pointerdown', { bubbles: true }), {
          pointerType: 'touch',
          clientX: x,
        }),
      )
    at(6)
    expect(sheet).not.toHaveBeenCalled()
    at(200)
    expect(sheet).toHaveBeenCalledTimes(1)
  })
})
