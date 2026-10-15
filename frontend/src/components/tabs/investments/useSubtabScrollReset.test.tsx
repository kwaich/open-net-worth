// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render } from '@testing-library/react'
import { useState } from 'react'
import { useSubtabScrollReset } from './useSubtabScrollReset'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function Probe({ tab }: { tab: string }) {
  const [el, setEl] = useState<HTMLDivElement | null>(null)
  useSubtabScrollReset(tab, el)
  return (
    <div data-testid="scroller" style={{ overflowY: 'auto' }}>
      <div ref={setEl} data-testid="tabwrap" className="mk-tabwrap" />
    </div>
  )
}

// A scroll container whose tab wrapper naturally starts 400px down; `scrolled` is its offset.
function setup(scrolled: number) {
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(780)
  const view = render(<Probe tab="holdings" />)
  const scroller = view.getByTestId('scroller')
  scroller.scrollTop = scrolled
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLElement,
  ) {
    const top = this.classList.contains('mk-tabwrap') ? 400 - scroller.scrollTop : 0
    return {
      top,
      bottom: top,
      left: 0,
      right: 0,
      width: 0,
      height: 0,
      x: 0,
      y: top,
      toJSON: () => ({}),
    }
  })
  return { ...view, scroller }
}

describe('useSubtabScrollReset', () => {
  it('keeps the tab wrapper at least one scroller-viewport tall, so pinning stays reachable', () => {
    const { getByTestId } = setup(0)
    // A short tab (a 4-name watchlist) could otherwise not scroll far enough to keep the bar
    // pinned — the browser clamped scrollTop and the page jumped up.
    expect(getByTestId('tabwrap').style.minHeight).toBe('780px')
  })

  it('scrolls back to where the tabs start sticking when switching while stuck', () => {
    const { scroller, rerender } = setup(829)
    rerender(<Probe tab="watchlist" />)
    expect(scroller.scrollTop).toBe(400)
  })

  it('cancels a running fling for one frame, then restores scrolling', () => {
    const frames: FrameRequestCallback[] = []
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      frames.push(cb)
      return frames.length
    })
    const { scroller, rerender } = setup(829)
    rerender(<Probe tab="watchlist" />)
    expect(scroller.style.overflowY).toBe('hidden')
    act(() => frames.forEach((cb) => cb(0)))
    expect(scroller.style.overflowY).toBe('auto')
    expect(scroller.scrollTop).toBe(400)
  })

  it('leaves the scroll alone above the sticking point', () => {
    const { scroller, rerender } = setup(120)
    rerender(<Probe tab="watchlist" />)
    expect(scroller.scrollTop).toBe(120)
  })

  it('does nothing on a re-render that keeps the same tab', () => {
    const { scroller, rerender } = setup(829)
    rerender(<Probe tab="holdings" />)
    expect(scroller.scrollTop).toBe(829)
  })
})
