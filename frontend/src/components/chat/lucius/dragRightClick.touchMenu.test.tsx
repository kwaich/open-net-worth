// @vitest-environment jsdom
/**
 * Regression tests for the touch/edge behaviour of the "Ask Lucius about this" context menu
 * (dragRightClick.tsx + ctxMenu.tsx). Owner report from an iPhone:
 *
 *   1. A long-press on a holding row opened OUR menu AND iOS's own text-selection callout
 *      (Copy / Look Up / Translate) on top of it — nothing suppressed the native gesture.
 *   2. The menu rendered cut off when the press was near a viewport edge.
 *   3. Tapping the row afterwards navigated to the detail screen but the menu stayed open.
 *
 * Plus two latent bugs found in the same hook: the click iOS synthesizes on lift-off after a
 * long-press ran the row's own onClick (opening the detail behind the menu), and Android
 * Chrome fires `contextmenu` for the same long-press, re-opening the menu at a second position.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, fireEvent, cleanup, act } from '@testing-library/react'
import { DragRightClick } from './dragRightClick'

vi.mock('../../../state/chatComposer', () => ({
  useChatComposer: () => ({ addMention: vi.fn() }),
}))

const MENU_W = 210
const MENU_H = 44

function mountRow(): HTMLElement {
  const el = document.createElement('div')
  el.setAttribute('data-entity-id', 'position:ORCL')
  el.setAttribute('data-entity-label', 'Oracle Corporation')
  document.body.appendChild(el)
  return el
}

function menu(): HTMLElement | null {
  return document.querySelector<HTMLElement>('.lx-ctxmenu')
}

function longPress(el: HTMLElement, x: number, y: number) {
  fireEvent.touchStart(el, { touches: [{ clientX: x, clientY: y }] })
  act(() => {
    vi.advanceTimersByTime(500)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLElement,
  ) {
    const left = parseFloat(this.style.left) || 0
    const top = parseFloat(this.style.top) || 0
    return {
      x: left,
      y: top,
      left,
      top,
      width: MENU_W,
      height: MENU_H,
      right: left + MENU_W,
      bottom: top + MENU_H,
      toJSON: () => ({}),
    } as DOMRect
  })
})

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('long-press → context menu (touch path)', () => {
  it('opens the menu after 500ms and clears any native text selection that raced in', () => {
    const removeAllRanges = vi.fn()
    vi.spyOn(window, 'getSelection').mockReturnValue({
      removeAllRanges,
    } as unknown as Selection)
    render(<DragRightClick />)
    const row = mountRow()

    longPress(row, 100, 300)

    expect(menu()).not.toBeNull()
    expect(removeAllRanges).toHaveBeenCalled()
  })

  it('swallows the click iOS synthesizes on lift-off, so the row does not navigate', () => {
    render(<DragRightClick />)
    const row = mountRow()
    const rowClick = vi.fn()
    row.addEventListener('click', rowClick)

    longPress(row, 100, 300)
    fireEvent.touchEnd(row)
    fireEvent.click(row)

    expect(rowClick).not.toHaveBeenCalled()
    expect(menu()).not.toBeNull()
  })

  it('does not swallow an ordinary tap that was never a long-press', () => {
    render(<DragRightClick />)
    const row = mountRow()
    const rowClick = vi.fn()
    row.addEventListener('click', rowClick)

    fireEvent.touchStart(row, { touches: [{ clientX: 100, clientY: 300 }] })
    act(() => {
      vi.advanceTimersByTime(120)
    })
    fireEvent.touchEnd(row)
    fireEvent.click(row)

    expect(rowClick).toHaveBeenCalledTimes(1)
    expect(menu()).toBeNull()
  })

  it('ignores the contextmenu Android fires for the same long-press (no second placement)', () => {
    render(<DragRightClick />)
    const row = mountRow()

    longPress(row, 100, 300)
    const before = { left: menu()!.style.left, top: menu()!.style.top }

    const ctx = fireEvent.contextMenu(row, { clientX: 100, clientY: 300 })

    expect(ctx).toBe(false)
    expect(menu()!.style.left).toBe(before.left)
    expect(menu()!.style.top).toBe(before.top)
  })

  it('opens ONCE when Android contextmenu lands before our 500ms timer (reverse ordering)', () => {
    render(<DragRightClick />)
    const row = mountRow()

    fireEvent.touchStart(row, { touches: [{ clientX: 100, clientY: 300 }] })
    act(() => {
      vi.advanceTimersByTime(480)
    })
    fireEvent.contextMenu(row, { clientX: 100, clientY: 300 })

    // Placed at the touch position (above the finger), not at the raw contextmenu point.
    expect(document.querySelectorAll('.lx-ctxmenu').length).toBe(1)
    expect(parseFloat(menu()!.style.top)).toBe(300 - 56)

    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(document.querySelectorAll('.lx-ctxmenu').length).toBe(1)
    expect(parseFloat(menu()!.style.top)).toBe(300 - 56)
  })
})

describe('context menu placement near a viewport edge', () => {
  it('clamps inside the viewport when opened at the bottom-right corner', () => {
    render(<DragRightClick />)
    const row = mountRow()

    fireEvent.contextMenu(row, { clientX: window.innerWidth - 4, clientY: window.innerHeight - 4 })

    const m = menu()!
    expect(parseFloat(m.style.left)).toBe(window.innerWidth - MENU_W - 8)
    expect(parseFloat(m.style.top)).toBe(window.innerHeight - MENU_H - 8)
  })

  it('never goes above/left of the 8px margin (a touch near the top edge)', () => {
    render(<DragRightClick />)
    const row = mountRow()

    longPress(row, 2, 10)

    const m = menu()!
    expect(parseFloat(m.style.left)).toBe(8)
    expect(parseFloat(m.style.top)).toBe(8)
  })
})

describe('context menu dismissal', () => {
  it('closes on the very next pointerdown outside it — no arming delay', () => {
    render(<DragRightClick />)
    const row = mountRow()

    longPress(row, 100, 300)
    expect(menu()).not.toBeNull()

    fireEvent.pointerDown(row)

    expect(menu()).toBeNull()
  })

  it('stays open on a pointerdown inside the menu itself', () => {
    render(<DragRightClick />)
    const row = mountRow()

    longPress(row, 100, 300)
    fireEvent.pointerDown(menu()!.querySelector('button')!)

    expect(menu()).not.toBeNull()
  })

  it('closes on Escape', () => {
    render(<DragRightClick />)
    const row = mountRow()

    longPress(row, 100, 300)
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(menu()).toBeNull()
  })

  it('closes when the row it was opened from leaves the DOM (screen navigated away)', async () => {
    render(<DragRightClick />)
    const row = mountRow()

    longPress(row, 100, 300)
    expect(menu()).not.toBeNull()

    await act(async () => {
      row.remove()
      await Promise.resolve()
    })

    expect(menu()).toBeNull()
  })
})

describe('phone stylesheet suppresses the native long-press gesture on entity rows', () => {
  it('disables text selection and the WebKit callout for [data-entity-id] below the phone breakpoint', () => {
    const css = readFileSync(resolve(__dirname, '../../../index.css'), 'utf8')
    const start = css.indexOf('@media (max-width: 639.98px)')
    expect(start).toBeGreaterThan(-1)
    const block = css.slice(start, css.indexOf('[data-entity-id].mb-pressed', start))
    const rule = block.match(/\[data-entity-id\]\s*\{[^}]*\}/g)?.at(-1) ?? ''
    expect(rule).toMatch(/-webkit-touch-callout:\s*none/)
    expect(rule).toMatch(/-webkit-user-select:\s*none/)
    expect(rule).toMatch(/\buser-select:\s*none/)
  })
})
