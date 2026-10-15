// @vitest-environment jsdom
//
// The delegated chart tooltip on TOUCH: a finger cannot hover, so the first tap on a
// `data-tip` element pins the popup and is swallowed (the element's own click never fires);
// a second tap on the same element lets the click through; a tap elsewhere dismisses it.
// A mouse click is never intercepted.

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { _resetOvTooltipForTests, installOvTooltip } from './tooltip'

function pointerDown(target: Element, pointerType: string) {
  const ev = new Event('pointerdown', { bubbles: true })
  Object.defineProperty(ev, 'pointerType', { value: pointerType })
  target.dispatchEvent(ev)
}

function click(target: Element) {
  const ev = new MouseEvent('click', { bubbles: true, cancelable: true, clientX: 40, clientY: 40 })
  target.dispatchEvent(ev)
  return ev
}

function tap(target: Element) {
  pointerDown(target, 'touch')
  return click(target)
}

function box(): HTMLElement {
  return document.querySelector('.ov-htip') as HTMLElement
}

describe('installOvTooltip — touch', () => {
  let tile: HTMLElement
  let other: HTMLElement
  let onClick: ReturnType<typeof vi.fn>

  beforeAll(() => installOvTooltip())

  // Remove only the fixture: the tooltip module caches its own `.ov-htip` node on body, exactly
  // as it does in the app, where body is never wiped.
  afterEach(() => {
    _resetOvTooltipForTests()
    document.getElementById('grid')?.remove()
  })

  function mount() {
    const grid = document.createElement('div')
    grid.id = 'grid'
    grid.innerHTML = `
        <div id="tile" data-tip="Acme Corp\nS$1,000 • 40.0% of shown" data-tip-tap="Tap again to open"></div>
        <div id="other">elsewhere</div>`
    document.body.appendChild(grid)
    tile = document.getElementById('tile')!
    other = document.getElementById('other')!
    onClick = vi.fn()
    // Bubbling listener on the container stands in for React's root listener.
    document.getElementById('grid')!.addEventListener('click', onClick)
  }

  it('first tap pins the popup and swallows the click; second tap lets it through', () => {
    mount()
    const first = tap(tile)
    expect(first.defaultPrevented).toBe(true)
    expect(onClick).not.toHaveBeenCalled()
    expect(box().style.opacity).toBe('1')
    expect(box().textContent).toContain('Acme Corp')
    expect(box().textContent).toContain('Tap again to open')

    const second = tap(tile)
    expect(second.defaultPrevented).toBe(false)
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(box().style.opacity).toBe('0')
  })

  it('a tap elsewhere dismisses the pinned popup and is not swallowed', () => {
    mount()
    tap(tile)
    expect(box().style.opacity).toBe('1')
    const away = tap(other)
    expect(away.defaultPrevented).toBe(false)
    expect(onClick).toHaveBeenCalledTimes(1)
    expect(box().style.opacity).toBe('0')
  })

  it('a mouse click after a tap unpins, so hover tooltips come back (touch → trackpad handoff)', () => {
    mount()
    tap(tile)
    expect(box().style.opacity).toBe('1')

    pointerDown(other, 'mouse')
    const ev = click(other)
    expect(ev.defaultPrevented).toBe(false)
    expect(box().style.opacity).toBe('0')

    // Hover works again — and shows the hovered element, not the stale pinned one.
    other.setAttribute('data-tip', 'Elsewhere')
    other.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 40, clientY: 40 }))
    expect(box().style.opacity).toBe('1')
    expect(box().textContent).toContain('Elsewhere')
    expect(box().textContent).not.toContain('Acme Corp')
  })

  it('a mouse click is never intercepted and never shows the tap hint', () => {
    mount()
    pointerDown(tile, 'mouse')
    const ev = click(tile)
    expect(ev.defaultPrevented).toBe(false)
    expect(onClick).toHaveBeenCalledTimes(1)
    const move = new MouseEvent('mousemove', { bubbles: true, clientX: 40, clientY: 40 })
    tile.dispatchEvent(move)
    expect(box().style.opacity).toBe('1')
    expect(box().textContent).not.toContain('Tap again to open')
  })
})
