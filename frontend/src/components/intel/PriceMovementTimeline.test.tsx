// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { PriceMovementTimeline } from './PriceMovementTimeline'
import { clockIn } from './movementFormat'
import {
  _clearMovementCache,
  PENDING_POLL_MS,
  pollIntervalFor,
} from '../../hooks/useMovementTimeline'
import type { MovementRow, MovementTimeline } from '../../types/assetDetail'

vi.mock('../../api/client', () => ({
  authFetch: vi.fn(),
}))
import { authFetch } from '../../api/client'

afterEach(cleanup)
beforeEach(() => {
  _clearMovementCache()
  vi.mocked(authFetch).mockReset()
})

function row(over: Partial<MovementRow>): MovementRow {
  return {
    kind: 'session',
    session_date: '2026-09-17',
    as_of: null,
    phase: 'closed',
    price: 70.87,
    change_pct: -0.14,
    prev_close: 70.97,
    after_hours: null,
    summary: null,
    sources: [],
    narrated_at: null,
    narration: 'none',
    rewriting: false,
    notable: false,
    ...over,
  }
}

function payload(rows: MovementRow[], state = 'CLOSED'): MovementTimeline {
  return {
    instrument_id: 'i1',
    ticker: 'UBER',
    label: 'Uber',
    currency: 'USD',
    market: {
      state,
      is_open: state === 'REGULAR',
      is_regular: state === 'REGULAR',
      timezone: 'America/New_York',
      tz_short: 'ET',
      reopens_at: null,
    },
    today: '2026-09-20',
    as_of: '2026-09-20T04:21:00Z',
    rows,
  }
}

function respond(body: MovementTimeline | null, status = 200) {
  vi.mocked(authFetch).mockResolvedValue({
    ok: status === 200,
    status,
    json: async () => body,
  } as unknown as Response)
}

describe('PriceMovementTimeline', () => {
  it('says "Updating…" with a spinner beside a paragraph being rewritten, and polls fast', async () => {
    // Owner 2026-09-21: "when the narrative is regenerating there is no way to tell — we
    // are still showing the old narrative of today". The old text stays (it is the last
    // known), the row says it is being rewritten, and the poll drops to the pending cadence.
    respond(
      payload(
        [
          row({
            kind: 'live',
            session_date: '2026-09-21',
            phase: 'regular',
            price: 148.9,
            change_pct: 0.88,
            summary: 'Oracle is trading higher in pre-market.',
            sources: [{ url: 'https://a.com/1', title: 'A', domain: 'a.com' }],
            narrated_at: '2026-09-21T09:25:07Z',
            narration: 'ready',
            rewriting: true,
          }),
        ],
        'REGULAR',
      ),
    )
    render(<PriceMovementTimeline instrumentId="i1" hasTicker />)
    await screen.findByText('Oracle is trading higher in pre-market.')
    expect(screen.getByText('Updating…')).toBeTruthy()
    expect(document.querySelector('.asd-pm-updating .ui-btn__spinner')).not.toBeNull()
    expect(
      pollIntervalFor(payload([row({ rewriting: true, summary: 'x', narration: 'ready' })])),
    ).toBe(PENDING_POLL_MS)
  })

  it("stamps when the paragraph was generated, in the venue's clock, under the sources pill", async () => {
    // Owner ask 2026-09-21: ORCL's row at 12:35 PM still carried a paragraph written at 5:25 AM
    // ("in Monday's pre-market session"); a visible "Generated 5:25 AM EDT" is what tells the
    // reader that. A paragraph from an earlier day carries its date too.
    respond(
      payload(
        [
          row({
            kind: 'live',
            session_date: '2026-09-21',
            phase: 'regular',
            price: 148.9,
            change_pct: 0.88,
            summary: 'Oracle is trading higher in pre-market.',
            sources: [{ url: 'https://a.com/1', title: 'A', domain: 'a.com' }],
            narrated_at: '2026-09-21T09:25:07Z',
            narration: 'ready',
          }),
          row({
            session_date: '2026-09-18',
            summary: 'Oracle closed lower.',
            sources: [{ url: 'https://a.com/2', title: 'B', domain: 'a.com' }],
            narrated_at: '2026-09-19T11:02:00Z',
            narration: 'ready',
          }),
        ],
        'REGULAR',
      ),
    )
    vi.useFakeTimers({ shouldAdvanceTime: true })
    vi.setSystemTime(new Date('2026-09-21T16:35:00Z')) // 12:35 PM ET, 7h10m after 5:25 AM
    try {
      render(<PriceMovementTimeline instrumentId="i1" hasTicker />)
      await screen.findByText('Oracle is trading higher in pre-market.')
      expect(screen.getByText('Generated 7 hours ago')).toBeTruthy() // inside 24h: relative
      const stamps = Array.from(document.querySelectorAll('.asd-pm-generated')).map(
        (e) => e.textContent?.replace(/\s+/g, ' ') ?? '',
      )
      expect(stamps[1]).toContain('Generated Sep 19, 7:02 AM ET') // older: dated, venue clock
    } finally {
      vi.useRealTimers()
    }
  })

  it("rounds an equity price to 2dp — never the vendor's raw 3-4 decimal extended-hours print", async () => {
    // Owner-reproduced 2026-09-21: "$148.915" / "$681.6867" on US equities. Same precision
    // rule as the price header (AssetDetailView's priceMaxFrac): 2dp unless crypto.
    respond(
      payload([
        row({
          kind: 'live',
          session_date: '2026-09-21',
          phase: 'regular',
          price: 148.915,
          change_pct: 0.88,
          after_hours: null,
        }),
      ]),
    )
    render(<PriceMovementTimeline instrumentId="i1" hasTicker maxFrac={2} />)
    await screen.findByText('$148.92')
    expect(screen.queryByText('$148.915')).toBeNull()
  })

  it('shows the top fade only once the card has been scrolled — never over the first row at rest', async () => {
    // Owner ask 2026-09-21: a fade at the top to match the bottom one, but WITHOUT adding top
    // padding — so at rest (scrollTop 0) there is no fade sitting over the first row's date.
    respond(payload([row({ session_date: '2026-09-18' }), row({ session_date: '2026-09-17' })]))
    render(<PriceMovementTimeline instrumentId="i1" hasTicker />)
    const card = await screen.findByRole('region', { name: 'Notable price movement timeline' })
    expect(card.classList.contains('asd-pm-card--scrolled')).toBe(false)
    Object.defineProperty(card, 'scrollTop', { value: 24, configurable: true })
    fireEvent.scroll(card)
    expect(card.classList.contains('asd-pm-card--scrolled')).toBe(true)
    Object.defineProperty(card, 'scrollTop', { value: 0, configurable: true })
    fireEvent.scroll(card)
    expect(card.classList.contains('asd-pm-card--scrolled')).toBe(false)
  })

  it('renders the market-closed notice, the at-close · after-hours line and a session row', async () => {
    respond(
      payload([
        row({
          kind: 'market_closed',
          session_date: '2026-09-20',
          as_of: '2026-09-20T04:21:00Z',
          phase: 'closed',
        }),
        row({
          session_date: '2026-09-18',
          price: 70.5,
          change_pct: -0.52,
          after_hours: { price: 70.6, change_pct: 0.14, as_of: null },
          summary: 'Uber shares closed modestly lower, weighed by a California arbitration ruling.',
          sources: [
            { url: 'https://reuters.com/a', title: 'Ruling', domain: 'reuters.com' },
            { url: 'https://bloomberg.com/b', title: 'Insider buying', domain: 'bloomberg.com' },
          ],
          narration: 'ready',
        }),
        row({ session_date: '2026-09-17' }),
        row({ session_date: '2026-09-16', narration: 'failed' }),
      ]),
    )
    render(<PriceMovementTimeline instrumentId="i1" hasTicker />)
    await screen.findByText('Notable Price Movement')
    // 'none' and 'failed' are different sentences — a provider failure never reads as "quiet day".
    expect(screen.getAllByText('No notable coverage found for this session.')).toHaveLength(1)
    expect(screen.getByText(/Couldn't generate an explanation yet/)).toBeTruthy()
    expect(screen.getByText('Market Closed')).toBeTruthy()
    expect(screen.getByText('Market closed. Check back when trading resumes.')).toBeTruthy()
    expect(screen.getByText('12:21 AM')).toBeTruthy() // clock on one line…
    expect(screen.getByText('ET')).toBeTruthy() // …tz label on its own
    expect(screen.getAllByText('at Close')).toHaveLength(3) // every completed session says so
    expect(screen.getAllByText('After Hours')).toHaveLength(1) // only the row with a print
    expect(screen.getByText(/weighed by a California arbitration ruling/)).toBeTruthy()
    expect(screen.getByText('No notable coverage found for this session.')).toBeTruthy()
    // sources chip toggles the cited list
    fireEvent.click(screen.getByText('2 sources'))
    const link = screen.getByText('Ruling').closest('a') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe('https://reuters.com/a')
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('shows a live row with the session time and a generating skeleton while open', async () => {
    respond(
      payload(
        [
          row({
            kind: 'live',
            session_date: '2026-09-18',
            phase: 'regular',
            as_of: '2026-09-18T15:32:00Z',
            price: 71.12,
            change_pct: 0.88,
            narration: 'pending',
          }),
          row({ session_date: '2026-09-17' }),
        ],
        'REGULAR',
      ),
    )
    const { container } = render(<PriceMovementTimeline instrumentId="i1" hasTicker />)
    await screen.findByText('Open')
    expect(screen.getByText('11:32 AM')).toBeTruthy()
    expect(container.querySelector('.asd-pm-dot--live')).toBeTruthy()
    expect(screen.getByLabelText('Explanation generating')).toBeTruthy()
    expect(screen.queryByText(/Generating explanation/)).toBeNull() // skeleton only, no caption
    expect(screen.getByText('0.88%')).toBeTruthy()
  })

  it('labels a pre-market print', async () => {
    respond(
      payload(
        [
          row({
            kind: 'live',
            phase: 'pre',
            as_of: '2026-09-18T12:10:00Z',
            price: 71.2,
            change_pct: 0.47,
            narration: 'pending',
          }),
        ],
        'PRE',
      ),
    )
    render(<PriceMovementTimeline instrumentId="i1" hasTicker />)
    await screen.findByText('Notable Price Movement')
    expect(screen.getAllByText('Pre-Market').length).toBeGreaterThanOrEqual(2) // header chip + row tag
  })

  it('hides itself for a no-ticker holding and on a 404', async () => {
    const { container } = render(<PriceMovementTimeline instrumentId="i1" hasTicker={false} />)
    expect(container.textContent).toBe('')
    respond(null, 404)
    const second = render(<PriceMovementTimeline instrumentId="i2" hasTicker />)
    await waitFor(() => expect(second.container.textContent).toBe(''))
  })

  it('loads the next page as the reader nears the bottom, appending without a blank skeleton', async () => {
    class FakeIO {
      static instances: FakeIO[] = []
      cb: IntersectionObserverCallback
      constructor(cb: IntersectionObserverCallback) {
        this.cb = cb
        FakeIO.instances.push(this)
      }
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return []
      }
      fire() {
        this.cb([{ isIntersecting: true } as IntersectionObserverEntry], this as never)
      }
    }
    vi.stubGlobal('IntersectionObserver', FakeIO)
    try {
      const many = Array.from({ length: 10 }, (_, i) =>
        row({ session_date: `2026-09-${String(20 - i).padStart(2, '0')}` }),
      )
      respond(payload(many))
      render(<PriceMovementTimeline instrumentId="i1" hasTicker />)
      await screen.findByText(/Sep(t)?\.? 11/)
      expect(screen.queryByText('Show more')).toBeNull() // no button: it's scroll-driven
      expect(FakeIO.instances.length).toBeGreaterThan(0) // a full page → watching the sentinel
      // Reader reaches the bottom of the box → the next page is requested…
      const sentinel = FakeIO.instances[FakeIO.instances.length - 1]
      if (!sentinel) throw new Error('no IntersectionObserver was created')
      sentinel.fire()
      await waitFor(() =>
        expect(vi.mocked(authFetch).mock.calls.some(([u]) => String(u).includes('limit=20'))).toBe(
          true,
        ),
      )
      // …while the rows already on screen stay put (no whole-card skeleton).
      expect(screen.getByText(/Sep(t)?\.? 20/)).toBeTruthy()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('re-arms the poll at the fast cadence as soon as a pending row is on screen', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      respond(payload([row({ session_date: '2026-09-17', narration: 'pending' })]))
      render(<PriceMovementTimeline instrumentId="i1" hasTicker />)
      await vi.waitFor(() => expect(vi.mocked(authFetch)).toHaveBeenCalledTimes(1))
      await vi.waitFor(() => expect(screen.getByLabelText('Explanation generating')).toBeTruthy())
      // The FIRST timer must already be the 15 s pending cadence, not the 5-minute closed one.
      await vi.advanceTimersByTimeAsync(PENDING_POLL_MS + 500)
      expect(vi.mocked(authFetch).mock.calls.length).toBeGreaterThanOrEqual(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('refetches when the tab becomes visible again', async () => {
    respond(payload([row({ session_date: '2026-09-17', narration: 'pending' })]))
    render(<PriceMovementTimeline instrumentId="i1" hasTicker />)
    await screen.findByLabelText('Explanation generating')
    const before = vi.mocked(authFetch).mock.calls.length
    document.dispatchEvent(new Event('visibilitychange'))
    await waitFor(() => expect(vi.mocked(authFetch).mock.calls.length).toBe(before + 1))
  })

  it('does not watch for more when the page came back short (history ran out)', async () => {
    class FakeIO {
      static count = 0
      constructor() {
        FakeIO.count++
      }
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return []
      }
    }
    vi.stubGlobal('IntersectionObserver', FakeIO)
    try {
      respond(payload([row({ session_date: '2026-09-18' }), row({ session_date: '2026-09-17' })]))
      render(<PriceMovementTimeline instrumentId="i1" hasTicker />)
      await screen.findByText(/Sep(t)?\.? 17/)
      expect(FakeIO.count).toBe(0)
    } finally {
      vi.unstubAllGlobals()
    }
  })
})

describe('formatters', () => {
  it('clockIn renders in the venue timezone with its short label', () => {
    const nbsp = /\u00A0/g
    expect(clockIn('2026-09-20T04:21:00Z', 'America/New_York', 'ET').replace(nbsp, ' ')).toBe(
      '12:21 AM ET',
    )
    // The clock is one unbreakable token; the only ordinary space precedes the tz label.
    const cest = clockIn('2026-09-20T08:14:00Z', 'Europe/Berlin', 'CEST')
    expect(cest.split(' ')).toEqual(['10:14\u00A0AM', 'CEST'])
    expect(clockIn('2026-09-20T04:21:00Z', 'Not/AZone', null)).toMatch(/\d/)
    expect(clockIn(null, null, null)).toBe('')
  })
})
