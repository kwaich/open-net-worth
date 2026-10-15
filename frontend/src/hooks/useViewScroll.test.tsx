// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { useViewScroll } from './useViewScroll'

// `remember` is what a forward navigation calls first — a button stands in for that tap.
function Probe({ view }: { view: string }) {
  const remember = useViewScroll(view)
  return (
    <div className="app-layout">
      <button onClick={remember}>go</button>
    </div>
  )
}

const windowScroll = vi.fn()
beforeEach(() => {
  // jsdom keeps scrollTop on an element but implements neither Element.scrollTo nor a real
  // window scroll — scrollTo moves the element, like a phone's .app-layout does.
  Element.prototype.scrollTo = function (this: Element, opts?: ScrollToOptions | number) {
    this.scrollTop = typeof opts === 'object' ? (opts.top ?? 0) : 0
  }
  vi.spyOn(window, 'scrollTo').mockImplementation(windowScroll)
})
afterEach(() => {
  cleanup()
  delete (Element.prototype as { scrollTo?: unknown }).scrollTo
  vi.restoreAllMocks()
  windowScroll.mockClear()
  Object.defineProperty(window, 'scrollY', { value: 0, configurable: true })
})

function setup(view: string) {
  const { container, rerender, getByRole } = render(<Probe view={view} />)
  const layout = container.querySelector('.app-layout') as HTMLElement
  // Forward: the offset is read BEFORE the next view is committed, then the view changes.
  const forward = (to: string) => {
    fireEvent.click(getByRole('button'))
    rerender(<Probe view={to} />)
  }
  const back = (to: string) => rerender(<Probe view={to} />)
  return { layout, forward, back, rerender }
}

describe('useViewScroll', () => {
  it('never scrolls on the first render or while the view stays the same', () => {
    const { layout, rerender } = setup('cat:all')
    layout.scrollTop = 300
    rerender(<Probe view="cat:all" />)
    expect(layout.scrollTop).toBe(300)
    expect(windowScroll).not.toHaveBeenCalled()
  })

  it('opens a view you have not been to at its top', () => {
    const { layout, forward } = setup('cat:all')
    layout.scrollTop = 814
    forward('cat:art')
    expect(layout.scrollTop).toBe(0)
  })

  it('returns to each view where it was left, whichever way back is done', () => {
    const { layout, forward, back } = setup('cat:all')
    layout.scrollTop = 200
    forward('cat:art')
    layout.scrollTop = 900
    forward('asset:a1')
    expect(layout.scrollTop).toBe(0)
    back('cat:art')
    expect(layout.scrollTop).toBe(900)
    back('cat:all')
    expect(layout.scrollTop).toBe(200)
  })

  it('uses an offset once — reopening a category later starts at its top again', () => {
    const { layout, forward, back } = setup('cat:all')
    layout.scrollTop = 200
    forward('cat:art')
    layout.scrollTop = 900
    forward('asset:a1')
    back('cat:art')
    back('cat:all')
    forward('cat:art')
    expect(layout.scrollTop).toBe(0)
  })

  it("reads and restores the window's scroll too — a desktop scrolls the window", () => {
    const { forward, back } = setup('cat:all')
    Object.defineProperty(window, 'scrollY', { value: 1200, configurable: true })
    forward('asset:a1')
    expect(windowScroll).toHaveBeenLastCalledWith({ top: 0, left: 0, behavior: 'instant' })
    back('cat:all')
    expect(windowScroll).toHaveBeenLastCalledWith({ top: 1200, left: 0, behavior: 'instant' })
  })
})
