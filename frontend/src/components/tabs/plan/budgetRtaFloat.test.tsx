// @vitest-environment jsdom
//
// Owner, 2026-09-25: the floating Ready-to-Assign banner stopped appearing. The workspace
// chrome parks the hero with its last pixel under the pinned toolbar, and a plain "intersecting
// at all" test read that sliver as the hero still being on screen. It now counts as gone once
// less than a tenth of it shows.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import { RtaFloat } from './budgetRtaFloat'

type Fire = (entries: Array<Partial<IntersectionObserverEntry>>) => void
let fire: Fire = () => {}
class FakeIntersectionObserver {
  constructor(cb: Fire) {
    fire = cb
  }
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return []
  }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

function mountFloat() {
  vi.stubGlobal('IntersectionObserver', FakeIntersectionObserver)
  for (const cls of ['pl-summary', 'pl-budget-main']) {
    const el = document.createElement('div')
    el.className = cls
    document.body.appendChild(el)
  }
  render(
    <RtaFloat
      rta={100}
      overspentCount={0}
      underfundedCount={0}
      totalAvailable={50}
      filter={null}
      setFilter={vi.fn()}
    />,
  )
  return () => document.querySelector('.pl-rta-float')!.classList.contains('shown')
}

describe('RtaFloat — the hero scrolling away', () => {
  it('shows once only a sliver of the hero is left (the parked chrome keeps 1px of it)', () => {
    const shown = mountFloat()
    act(() => fire([{ isIntersecting: true, intersectionRatio: 1 / 131 }]))
    expect(shown()).toBe(true)
  })

  it('stays hidden while the hero is on screen', () => {
    const shown = mountFloat()
    act(() => fire([{ isIntersecting: true, intersectionRatio: 0.6 }]))
    expect(shown()).toBe(false)
  })
})

describe('RtaFloat — another Plan sub-tab showing', () => {
  it('hides once the hero stops rendering (Outlook/Cash Flow hide Budget with display:none)', () => {
    let resized: () => void = () => {}
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(cb: () => void) {
          resized = cb
        }
        observe() {}
        disconnect() {}
      },
    )
    const shown = mountFloat()
    const hero = document.querySelector('.pl-summary') as HTMLElement
    let rects = 1
    hero.getClientRects = () => ({ length: rects }) as DOMRectList
    act(() => fire([{ isIntersecting: false, intersectionRatio: 0 }]))
    act(() => resized())
    expect(shown()).toBe(true)
    rects = 0
    act(() => resized())
    expect(shown()).toBe(false)
  })
})
