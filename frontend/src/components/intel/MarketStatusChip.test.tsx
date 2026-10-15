// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { MarketStatusChip } from './MarketStatusChip'
import type { MarketStatus } from '../../types/assetDetail'

const shellCss = readFileSync(resolve(__dirname, '../../index.css'), 'utf8')

const base: MarketStatus = {
  state: 'REGULAR',
  is_open: true,
  is_regular: true,
  exchange: 'NasdaqGS',
  timezone: 'America/New_York',
  tz_short: 'EDT',
  gmt_offset: -14400000,
  reopens_at: null,
}

afterEach(cleanup)

describe('MarketStatusChip', () => {
  it('renders Open with timezone for a regular session, no exchange (owned by the subline next to it)', () => {
    const { container, getByText } = render(<MarketStatusChip market={base} />)
    expect(getByText('Open')).toBeTruthy()
    expect(container.querySelector('.asd-market--open')).toBeTruthy()
    expect(container.textContent).not.toContain('NasdaqGS')
    expect(container.textContent).toContain('EDT')
  })

  it('shows extended-hours tone for POST and no reopen hint (still open)', () => {
    const { container, getByText } = render(
      <MarketStatusChip market={{ ...base, state: 'POST', is_regular: false }} />,
    )
    expect(getByText('After hours')).toBeTruthy()
    expect(container.querySelector('.asd-market--ext')).toBeTruthy()
    expect(container.textContent).not.toMatch(/reopens/i)
  })

  it('shows closed tone + a reopens countdown when closed', () => {
    const future = new Date(Date.now() + 2 * 3600_000).toISOString() // +2h
    const { container, getByText } = render(
      <MarketStatusChip
        market={{ ...base, state: 'CLOSED', is_open: false, is_regular: false, reopens_at: future }}
      />,
    )
    expect(getByText('Market closed')).toBeTruthy()
    expect(container.querySelector('.asd-market--closed')).toBeTruthy()
    expect(container.textContent).toMatch(/reopens in 2h/i)
  })

  it('treats PREPRE/POSTPOST (deep overnight) as Market closed', () => {
    for (const state of ['PREPRE', 'POSTPOST']) {
      const { container, getByText, unmount } = render(
        <MarketStatusChip
          market={{ ...base, state, is_open: false, is_regular: false, reopens_at: null }}
        />,
      )
      expect(getByText('Market closed')).toBeTruthy()
      expect(container.querySelector('.asd-market--closed')).toBeTruthy()
      unmount()
    }
  })

  it('renders 24/7 for crypto (always open)', () => {
    const { getByText } = render(
      <MarketStatusChip
        market={{
          ...base,
          exchange: 'Crypto',
          timezone: 'UTC',
          tz_short: 'UTC',
          always_open: true,
        }}
      />,
    )
    expect(getByText('24/7')).toBeTruthy()
  })

  it('shows a Delayed Nm tag when the exchange reports a licensed data delay (e.g. XETRA)', () => {
    const { container } = render(
      <MarketStatusChip
        market={{ ...base, exchange: 'XETRA', tz_short: 'CEST', delayed_minutes: 15 }}
      />,
    )
    expect(container.textContent).toContain('Delayed 15m')
  })

  it('renders the tz text and the Delayed tag as SEPARATE elements, not one joined span', () => {
    // Structural precondition for the narrow-header collapse below: the CSS collapse rule
    // hides `.asd-market-state` + `.asd-market-tz` only — for the Delayed tag to survive
    // that collapse, it must be its own element the collapse selector never names.
    const { container } = render(
      <MarketStatusChip
        market={{ ...base, exchange: 'XETRA', tz_short: 'CEST', delayed_minutes: 15 }}
      />,
    )
    const tz = container.querySelector('.asd-market-tz')
    const delayed = container.querySelector('.asd-market-delayed')
    expect(tz?.textContent).toBe('CEST')
    expect(delayed?.textContent).toBe('Delayed 15m')
    expect(container.querySelector('.asd-market-meta')).toBeNull() // the old joined span is gone
  })

  it('keeps the full tz + Delayed text reachable via the title attribute either way', () => {
    const { container } = render(
      <MarketStatusChip
        market={{ ...base, exchange: 'XETRA', tz_short: 'CEST', delayed_minutes: 15 }}
      />,
    )
    const chip = container.querySelector('.asd-market')
    expect(chip?.getAttribute('title')).toBe('Open • CEST • Delayed 15m')
  })

  it('omits the Delayed tag for a real-time venue (no delayed_minutes)', () => {
    const { container } = render(<MarketStatusChip market={base} />)
    expect(container.textContent).not.toMatch(/delayed/i)
  })

  it('shows an "opens in" countdown to regular open during PRE (moved here from the price caption, owner ask 2026-09-01)', () => {
    const soon = new Date(Date.now() + 90 * 60_000).toISOString() // +1h30m
    const { container } = render(
      <MarketStatusChip
        market={{
          ...base,
          state: 'PRE',
          is_open: true,
          is_regular: false,
          session_bounds: { regular: { start: soon, end: soon } },
        }}
      />,
    )
    expect(container.textContent).toMatch(/opens in 1h 30m/i)
  })

  it('shows a "closes in" countdown to regular close while REGULAR', () => {
    const soon = new Date(Date.now() + 25 * 60_000).toISOString() // +25m
    const { container } = render(
      <MarketStatusChip
        market={{ ...base, session_bounds: { regular: { start: soon, end: soon } } }}
      />,
    )
    expect(container.textContent).toMatch(/closes in 25m/i)
  })

  it('shows a "closes in" countdown to the post-market window\'s own end while POST', () => {
    const soon = new Date(Date.now() + 45 * 60_000).toISOString() // +45m
    const { container } = render(
      <MarketStatusChip
        market={{
          ...base,
          state: 'POST',
          is_regular: false,
          session_bounds: { post: { start: soon, end: soon } },
        }}
      />,
    )
    expect(container.textContent).toMatch(/closes in 45m/i)
  })

  it('shows no countdown once the target bound has already elapsed', () => {
    const past = new Date(Date.now() - 60_000).toISOString()
    const { container } = render(
      <MarketStatusChip
        market={{ ...base, session_bounds: { regular: { start: past, end: past } } }}
      />,
    )
    expect(container.textContent).not.toMatch(/closes|opens|reopens/i)
  })

  it('renders nothing for unknown / missing state', () => {
    const { container } = render(<MarketStatusChip market={{ ...base, state: 'UNKNOWN' }} />)
    expect(container.firstChild).toBeNull()
    const { container: c2 } = render(<MarketStatusChip market={null} />)
    expect(c2.firstChild).toBeNull()
  })

  describe('narrow-header collapse (index.css contract)', () => {
    // jsdom doesn't evaluate container/media queries, so the actual collapse can't be
    // exercised by rendering at a width — this asserts directly on the CSS source instead,
    // matching this repo's own precedent for responsive-CSS regression tests (see
    // SearchFilter.responsive.test.ts). Owner report 2026-09-08: "Delayed Nm" disappeared
    // into the bare dot on a narrow phone header, along with the state label and tz — it's
    // important enough that it must keep showing even when the header runs out of room.
    function narrowBlocks(css: string): string[] {
      const container =
        css.match(/@container asd-header \(width < 420px\) \{([\s\S]*?)\n {2}\}/)?.[1] ?? ''
      const media = css.match(/@media \(max-width: 420px\) \{([\s\S]*?)\n {2}\}/)?.[1] ?? ''
      return [container, media]
    }

    it('still collapses the state label and tz text to a bare dot', () => {
      for (const block of narrowBlocks(shellCss)) {
        expect(block).toMatch(
          /\.asd-market-state,\s*\n\s*\.asd-asset-subline-row \.asd-market-tz \{\s*\n\s*display: none;/,
        )
      }
    })

    it('never hides the Delayed tag at narrow widths', () => {
      for (const block of narrowBlocks(shellCss)) {
        expect(block).not.toContain('.asd-market-delayed')
      }
      // Belt and suspenders: no rule anywhere in the shell sets `.asd-market-delayed` itself
      // to `display: none` (as opposed to merely not mentioning it inside the narrow block).
      expect(shellCss).not.toMatch(/\.asd-market-delayed[^{]*\{\s*display:\s*none/)
    })
  })
})
