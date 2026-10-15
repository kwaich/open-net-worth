// @vitest-environment jsdom
//
// The Lucius transcript declares `scrollbar-gutter: stable both-edges` and pads
// `calc(16px - var(--lx-gutter))` so the VISIBLE inset is exactly 16px whether the OS
// reserves a classic scrollbar track (Windows, Linux, macOS "Always" / a mouse plugged in)
// or draws an overlay one that takes no space (macOS trackpad default). The helper under
// test is the one place that turns the element's real geometry into that variable.
import { describe, expect, it } from 'vitest'
import { applyScrollbarGutter, scrollbarGutterPx } from './scrollbarGutter'

function fakeBox(offsetWidth: number, clientWidth: number): HTMLElement {
  const el = document.createElement('div')
  Object.defineProperty(el, 'offsetWidth', { value: offsetWidth, configurable: true })
  Object.defineProperty(el, 'clientWidth', { value: clientWidth, configurable: true })
  return el
}

describe('scrollbarGutterPx', () => {
  it('is zero under overlay scrollbars, where the box reserves nothing', () => {
    expect(scrollbarGutterPx(fakeBox(400, 400))).toBe(0)
  })

  it('is HALF the reserved width — both-edges puts a phantom track on each side', () => {
    expect(scrollbarGutterPx(fakeBox(400, 378))).toBe(11)
  })

  it('never goes negative on a fractional or rounded-up clientWidth', () => {
    expect(scrollbarGutterPx(fakeBox(400, 401))).toBe(0)
  })
})

describe('applyScrollbarGutter', () => {
  it('writes the measured gutter as --lx-gutter on the target', () => {
    const target = document.createElement('aside')
    applyScrollbarGutter(fakeBox(400, 378), target)
    expect(target.style.getPropertyValue('--lx-gutter')).toBe('11px')
  })

  it('rewrites the variable when the mode flips, e.g. a mouse gets plugged in', () => {
    const target = document.createElement('aside')
    applyScrollbarGutter(fakeBox(400, 400), target)
    expect(target.style.getPropertyValue('--lx-gutter')).toBe('0px')
    applyScrollbarGutter(fakeBox(400, 380), target)
    expect(target.style.getPropertyValue('--lx-gutter')).toBe('10px')
  })

  it('leaves the style untouched when the value is unchanged, so a ResizeObserver cannot loop', () => {
    const target = document.createElement('aside')
    applyScrollbarGutter(fakeBox(400, 380), target)
    let writes = 0
    const orig = target.style.setProperty.bind(target.style)
    target.style.setProperty = ((...args: Parameters<typeof orig>) => {
      writes += 1
      return orig(...args)
    }) as typeof orig
    applyScrollbarGutter(fakeBox(400, 380), target)
    expect(writes).toBe(0)
  })
})
