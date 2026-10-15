// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const router = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => router.navigate }))

import { usePlanSubTab } from './usePlanSubTab'

// A router whose navigations land asynchronously, each in its own render, and echo back into
// `?sub=` as a new request — the shape that made the URL mirror and the deep-link effect
// ping-pong.
let lastLanding = 0

function Harness({ initialSub, onRender }: { initialSub?: string; onRender: () => void }) {
  const [sub, setSub] = useState(initialSub)
  useEffect(() => {
    router.navigate.mockImplementation(({ search }: { search: (p: object) => { sub: string } }) => {
      const next = search({}).sub
      lastLanding = Math.max(lastLanding, Date.now()) + 1
      setTimeout(() => setSub(next), lastLanding - Date.now())
    })
  }, [])
  const [tab] = usePlanSubTab(sub)
  onRender()
  return <div data-testid="tab">{tab}</div>
}

describe('usePlanSubTab', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    sessionStorage.clear()
    router.navigate.mockReset()
    lastLanding = 0
  })
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it('lets a ?sub= link beat the remembered tab and settles instead of ping-ponging', async () => {
    sessionStorage.setItem('pl-subtab', 'cashflow')
    const renders = vi.fn()
    render(<Harness initialSub="outlook" onRender={renders} />)
    expect(screen.getByTestId('tab').textContent).toBe('outlook')
    for (let i = 0; i < 200; i++) await act(async () => vi.advanceTimersByTime(1))
    expect(screen.getByTestId('tab').textContent).toBe('outlook')
    expect(sessionStorage.getItem('pl-subtab')).toBe('outlook')
    expect(renders.mock.calls.length).toBeLessThan(10)
  })

  it('falls back to the remembered tab when the link names no valid tab', () => {
    sessionStorage.setItem('pl-subtab', 'cashflow')
    render(<Harness initialSub="nonsense" onRender={() => {}} />)
    expect(screen.getByTestId('tab').textContent).toBe('cashflow')
  })

  it('defaults to budget with neither a link nor a remembered tab', () => {
    render(<Harness onRender={() => {}} />)
    expect(screen.getByTestId('tab').textContent).toBe('budget')
  })

  it('starts a fresh visit on Budget, ignoring the old forever-remembered tab', () => {
    localStorage.setItem('pl-subtab', 'outlook')
    render(<Harness onRender={() => {}} />)
    expect(screen.getByTestId('tab').textContent).toBe('budget')
    expect(localStorage.getItem('pl-subtab')).toBeNull()
  })
})
