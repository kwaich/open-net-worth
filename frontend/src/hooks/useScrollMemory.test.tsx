// @vitest-environment jsdom
//
// Back used to land at the top of the page: the shell scrolled to the top on EVERY path
// change. Each history entry now remembers its offset; Back/Forward restore it (retrying while
// the page fills in), and a new page opens at the top.

import { cleanup, render } from '@testing-library/react'
import { useRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Listener = (e: { location: Loc; action: { type: string } }) => void
type Loc = { pathname: string; state: { __TSR_key: string } }

const history = vi.hoisted(() => ({
  location: { pathname: '/dashboard', state: { __TSR_key: 'a' } } as Loc,
  listeners: [] as Listener[],
  subscribe(fn: Listener) {
    this.listeners.push(fn)
    return () => (this.listeners = this.listeners.filter((l) => l !== fn))
  },
  go(pathname: string, key: string, type: string) {
    this.location = { pathname, state: { __TSR_key: key } }
    for (const l of this.listeners) l({ location: this.location, action: { type } })
  },
}))
vi.mock('@tanstack/react-router', () => ({ useRouter: () => ({ history }) }))

import { useScrollMemory } from './useScrollMemory'

function Shell() {
  const ref = useRef<HTMLDivElement>(null)
  useScrollMemory(ref)
  return <div ref={ref} className="app-layout" />
}

function layout(): HTMLDivElement {
  return document.querySelector('.app-layout') as HTMLDivElement
}

/** jsdom does no layout: give the scroller a real scrollTop that scrollTo() moves. */
function scrollable(el: HTMLDivElement) {
  let top = 0
  Object.defineProperty(el, 'scrollTop', {
    get: () => top,
    set: (v) => (top = v),
    configurable: true,
  })
  el.scrollTo = ((opts: ScrollToOptions) => {
    top = opts.top ?? 0
    el.dispatchEvent(new Event('scroll'))
  }) as typeof el.scrollTo
}

beforeEach(() => {
  vi.useFakeTimers()
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo
  history.location = { pathname: '/dashboard', state: { __TSR_key: 'a' } }
  history.listeners = []
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('useScrollMemory', () => {
  it('opens a new page at the top and returns to the old offset on Back', () => {
    render(<Shell />)
    const el = layout()
    scrollable(el)
    el.scrollTo({ top: 1200 })

    history.go('/accounts', 'b', 'PUSH')
    vi.advanceTimersByTime(100)
    expect(el.scrollTop).toBe(0)

    history.go('/dashboard', 'a', 'BACK')
    vi.advanceTimersByTime(1000)
    expect(el.scrollTop).toBe(1200)
  })

  it('keeps its place when only the query changes (a filter, a sub-tab)', () => {
    render(<Shell />)
    const el = layout()
    scrollable(el)
    el.scrollTo({ top: 640 })
    history.go('/dashboard', 'c', 'REPLACE')
    vi.advanceTimersByTime(100)
    expect(el.scrollTop).toBe(640)
  })

  it('lets go the moment the user scrolls during a restore', () => {
    render(<Shell />)
    const el = layout()
    scrollable(el)
    el.scrollTo({ top: 900 })
    history.go('/accounts', 'b', 'PUSH')
    vi.advanceTimersByTime(100)

    // The page is still short when Back lands: the restore can't reach 900 yet.
    let max = 300
    el.scrollTo = ((opts: ScrollToOptions) => {
      el.scrollTop = Math.min(opts.top ?? 0, max)
    }) as typeof el.scrollTo
    history.go('/dashboard', 'a', 'BACK')
    vi.advanceTimersByTime(200)
    expect(el.scrollTop).toBe(300)

    window.dispatchEvent(new Event('wheel'))
    max = 5000
    vi.advanceTimersByTime(1000)
    expect(el.scrollTop).toBe(300)
  })
})
