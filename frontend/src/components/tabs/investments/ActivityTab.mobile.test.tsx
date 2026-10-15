// @vitest-environment jsdom
//
// Investments → Activity on a phone (owner, 2026-09-04): the type-chip row collapses into the
// shared FilterSheet button so search / range / filter share ONE line, and the 680px-wide
// ledger table gives way to a two-line card per event so nothing scrolls sideways. The wide
// layout is pinned too, so the swap can't silently regress the desktop table.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { ActivityRow } from './useActivityView'

const rows: ActivityRow[] = [
  row('a1', '2026-09-03', 'buy', 'RKLB', 'Rocket Lab', '25 @ 64.7 USD', 'IBKR', -2123.45),
  row('a2', '2026-08-31', 'sell', 'KSTL', 'Kestrel Dash', '500 @ 37.1 EUR', 'IBKR', 28210.5),
  row('a3', '2026-08-31', 'fee', 'KSTL', 'Kestrel Dash', 'disposal fee 3.18 EUR', 'IBKR', -4.68),
  row('a4', '2026-08-20', 'dividend', 'META', 'Meta Platforms', '10 × 0.5 USD', 'IBKR', 6.7),
  row('a5', '2026-08-15', 'interest', null, 'Cash interest', null, 'IBKR', 12.3),
  // The backend marks the no-cash rows (vests, transfers) `signed: false`.
  unsigned(
    row(
      'a6',
      '2026-07-15',
      'vest',
      'NET',
      'Cloudflare',
      '81.3125 units @ FMV 202.34 USD',
      'Schwab',
      16452.77,
    ),
  ),
  // A vest whose grant/lot could not be resolved (the backend's last-resort shape).
  unsigned(
    row('a7', '2026-06-15', 'vest', null, null, '81.3125 units @ FMV 198.1 USD', '', 16108.0),
  ),
  // Shares moved between the user's own accounts: a non-taxable $0 disposal that names its
  // destination — ONE row, no direction in the type, no cash either way.
  unsigned(
    row(
      'a8',
      '2026-07-25',
      'transfer',
      'KSTL',
      'Kestrel Dash',
      '1242 units → IBKR',
      'Workplace',
      0,
    ),
  ),
]

function unsigned(r: ActivityRow): ActivityRow {
  return { ...r, signed: false }
}

function row(
  id: string,
  date: string,
  type: string,
  symbol: string | null,
  name: string | null,
  detail: string | null,
  account: string,
  amount: number,
): ActivityRow {
  return {
    source_id: id,
    source_kind: 'test',
    date,
    type,
    symbol,
    name,
    detail,
    account_id: 'acc1',
    account_name: account,
    amount_base: amount,
    signed: true,
    currency: 'SGD',
    instrument_id: null,
    links: {},
  }
}

vi.mock('./useActivityView', () => ({
  useActivityView: () => ({
    data: {
      rows,
      total: rows.length,
      baseCurrency: 'SGD',
      realizedPnl: null,
      summary: { count30d: 5, netInvested30d: 0, income30d: 0, fees30d: 0, baseCurrency: 'SGD' },
    },
    loading: false,
    error: false,
    retry: () => {},
  }),
}))

/** Forces useIsMobile() — jsdom's default matchMedia stub reports desktop (vitest.setup.ts). */
function mockMobile(isMobile: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = ((query: string) => ({
    matches: isMobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

let restore: () => void = () => {}
beforeEach(() => {
  restore = () => {}
  // The fixtures are dated 2026 and a date in the current year is shown without its year.
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 9, 6))
})
afterEach(() => {
  cleanup()
  restore()
  vi.useRealTimers()
  document.body.style.overflow = ''
})

async function mount(isMobile: boolean) {
  restore = mockMobile(isMobile)
  const { ActivityTab } = await import('./ActivityTab')
  render(<ActivityTab />)
}

describe('ActivityTab — phone layout', () => {
  it('renders one card per event instead of the ledger table', async () => {
    await mount(true)
    expect(document.querySelector('table.act-tbl')).toBeNull()
    const cards = document.querySelectorAll('li.act-mcard')
    expect(cards).toHaveLength(rows.length)
  })

  it('a card reads symbol + type pill + amount on line one, detail + date · account on line two', async () => {
    await mount(true)
    const card = document.querySelector('li.act-mcard') as HTMLElement
    const main = card.querySelector('.act-mcard-main') as HTMLElement
    expect(within(main).getByText('RKLB')).toBeTruthy()
    expect(main.querySelector('.act-pill.buy')?.textContent).toBe('Buy')
    expect(card.querySelector('.act-mcard-amt')?.textContent).toMatch(/^−.*2,123\.45/)
    expect(card.querySelector('.act-mcard-sub')?.textContent).toBe('25 @ 64.7 USD')
    expect(card.querySelector('.act-mcard-meta')?.textContent).toBe('Sep 3 · IBKR')
  })

  it('a symbol-less event leads with its name rather than an empty title', async () => {
    await mount(true)
    const cards = [...document.querySelectorAll('li.act-mcard')]
    const interest = cards.find((c) => c.querySelector('.act-pill')?.textContent === 'Interest')!
    expect(interest.querySelector('.act-mcard-main .act-sym')?.textContent).toBe('Cash interest')
    expect(interest.querySelector('.act-mcard-sub')?.textContent).toBe('')
  })

  it('a vest reads like any other row — ticker, pill, units detail, date · account', async () => {
    await mount(true)
    const cards = [...document.querySelectorAll('li.act-mcard')]
    const vest = cards.find((c) => c.querySelector('.act-pill')?.textContent === 'Vest')!
    expect(vest.querySelector('.act-mcard-main .act-sym')?.textContent).toBe('NET')
    expect(vest.querySelector('.act-mcard-sub')?.textContent).toBe('81.3125 units @ FMV 202.34 USD')
    expect(vest.querySelector('.act-mcard-meta')?.textContent).toBe('Jul 15 · Schwab')
  })

  it('an unresolvable vest (no symbol, no name) leads with the pill alone — never "Vest Vest"', async () => {
    await mount(true)
    const cards = [...document.querySelectorAll('li.act-mcard')]
    const vest = cards.filter((c) => c.querySelector('.act-pill')?.textContent === 'Vest')[1]!
    expect(vest.querySelector('.act-mcard-main .act-sym')).toBeNull()
    expect(vest.querySelector('.act-mcard-main')?.textContent).toBe('Vest')
    expect(vest.querySelector('.act-mcard-sub')?.textContent).toBe('81.3125 units @ FMV 198.1 USD')
    expect(vest.querySelector('.act-mcard-meta')?.textContent).toBe('Jun 15 · —')
  })

  it('a transfer is its own row — "Transfer" pill, "→ account" detail, no signed amount', async () => {
    await mount(true)
    const cards = [...document.querySelectorAll('li.act-mcard')]
    const moved = cards.find((c) => c.querySelector('.act-pill')?.textContent === 'Transfer')!
    expect(moved.querySelector('.act-pill')?.classList.contains('transfer')).toBe(true)
    expect(moved.querySelector('.act-mcard-main .act-sym')?.textContent).toBe('KSTL')
    expect(moved.querySelector('.act-mcard-sub')?.textContent).toBe('1242 units → IBKR')
    expect(moved.querySelector('.act-mcard-amt')?.textContent).toBe('—')
    expect(moved.querySelector('.act-mcard-meta')?.textContent).toBe('Jul 25 · Workplace')
    // It is never a Sell, and it files under Transfers.
    expect(cards.filter((c) => c.querySelector('.act-pill')?.textContent === 'Sell')).toHaveLength(
      1,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    fireEvent.click(screen.getByRole('radio', { name: /Transfers/ }))
    const pills = [...document.querySelectorAll('li.act-mcard .act-pill')].map((p) => p.textContent)
    expect(pills).toEqual(['Transfer'])
  })

  it('an unsigned row (a vest) shows its figure without a +/− prefix', async () => {
    await mount(true)
    const cards = [...document.querySelectorAll('li.act-mcard')]
    const vest = cards.find((c) => c.querySelector('.act-pill')?.textContent === 'Vest')!
    expect(vest.querySelector('.act-mcard-amt')?.textContent).toMatch(/^S\$16,452\.77$/)
  })

  it('the filter button opens a bottom sheet listing every type group, and picking one narrows the cards', async () => {
    await mount(true)
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    const sheet = screen.getByRole('dialog', { name: 'Filter activity' })
    expect(sheet.classList.contains('kebab-sheet')).toBe(true)
    expect(document.body.style.overflow).toBe('hidden')
    const options = within(sheet)
      .getAllByRole('radio')
      .map((b) => b.textContent?.replace('✓', '').trim())
    expect(options).toEqual(['All', 'Buys', 'Sells', 'Income', 'Transfers', 'Vests', 'Fees'])

    fireEvent.click(within(sheet).getByRole('radio', { name: /Income/ }))
    expect(screen.queryByRole('dialog', { name: 'Filter activity' })).toBeNull()
    const pills = [...document.querySelectorAll('li.act-mcard .act-pill')].map((p) => p.textContent)
    expect(pills).toEqual(['Dividend', 'Interest'])
    // The trigger now advertises an active filter.
    expect(screen.getByRole('button', { name: 'Filters (active)' })).toBeTruthy()
  })

  it('the date-range button opens the same bottom sheet, and picking a range closes it', async () => {
    await mount(true)
    fireEvent.click(screen.getByRole('button', { name: 'Date range: Past 90d' }))
    expect(document.querySelector('.act-dd-menu')).toBeNull()
    const sheet = screen.getByRole('dialog', { name: 'Date range' })
    expect(sheet.classList.contains('kebab-sheet')).toBe(true)
    expect(document.body.style.overflow).toBe('hidden')
    const options = within(sheet)
      .getAllByRole('radio')
      .map((b) => b.textContent?.replace('✓', '').trim())
    expect(options).toHaveLength(13)
    expect(options.slice(0, 7)).toEqual([
      'Today',
      'Past 7 days',
      'Past 30 days',
      'Past 60 days',
      'Past 90 days',
      'Month-to-date',
      'Year-to-date',
    ])
    expect(options.slice(7).every((o) => /^Q[1-4] \d{4} • /.test(o ?? ''))).toBe(true)
    expect(
      within(sheet)
        .getByRole('radio', { name: /Past 90 days/ })
        .getAttribute('aria-checked'),
    ).toBe('true')

    fireEvent.click(within(sheet).getByRole('radio', { name: /Past 30 days/ }))
    expect(screen.queryByRole('dialog', { name: 'Date range' })).toBeNull()
    expect(document.body.style.overflow).toBe('')
    expect(screen.getByRole('button', { name: 'Date range: Past 30d' })).toBeTruthy()
  })

  it('the range sheet dismisses on Escape without changing the range', async () => {
    await mount(true)
    fireEvent.click(screen.getByRole('button', { name: 'Date range: Past 90d' }))
    expect(screen.getByRole('dialog', { name: 'Date range' })).toBeTruthy()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Date range' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Date range: Past 90d' })).toBeTruthy()
  })

  it('search still narrows the cards', async () => {
    await mount(true)
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search activity' }), {
      target: { value: 'kstl' },
    })
    const titles = [...document.querySelectorAll('li.act-mcard .act-mcard-main .act-sym')].map(
      (t) => t.textContent,
    )
    expect(titles).toEqual(['KSTL', 'KSTL', 'KSTL'])
  })

  it('shows the empty message as a card-list placeholder when nothing matches', async () => {
    await mount(true)
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search activity' }), {
      target: { value: 'zzz-no-match' },
    })
    expect(document.querySelectorAll('li.act-mcard')).toHaveLength(0)
    expect(document.querySelector('.act-cards-empty')?.textContent).toMatch(/No activity/)
  })
})

describe('ActivityTab — wide layout', () => {
  it('keeps the ledger table and the chip row; the filter opens as an anchored popover', async () => {
    await mount(false)
    expect(document.querySelector('table.act-tbl')).toBeTruthy()
    expect(document.querySelector('.act-cards')).toBeNull()
    expect(document.querySelectorAll('.act-filter .act-chip')).toHaveLength(7)
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    expect(
      screen
        .getByRole('dialog', { name: 'Filter activity' })
        .classList.contains('mk-filter-popover'),
    ).toBe(true)
    expect(document.body.style.overflow).toBe('')
  })

  it('the date-range button keeps its anchored menu — no bottom sheet on a wide layout', async () => {
    await mount(false)
    fireEvent.click(screen.getByRole('button', { name: 'Date range: Past 90d' }))
    expect(document.querySelector('.act-dd-menu')).toBeTruthy()
    expect(document.querySelector('.kebab-sheet')).toBeNull()
    expect(screen.queryByRole('dialog', { name: 'Date range' })).toBeNull()
    expect(document.body.style.overflow).toBe('')
    fireEvent.click(screen.getByRole('button', { name: 'Past 30 days' }))
    expect(document.querySelector('.act-dd-menu')).toBeNull()
    expect(screen.getByRole('button', { name: 'Date range: Past 30d' })).toBeTruthy()
  })

  it('the chips and the sheet share one filter state', async () => {
    await mount(false)
    fireEvent.click(screen.getByRole('button', { name: 'Filters' }))
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Filter activity' })).getByRole('radio', {
        name: /Fees/,
      }),
    )
    const onChip = document.querySelector('.act-chip.on')
    expect(onChip?.textContent).toBe('Fees')
    expect(document.querySelectorAll('table.act-tbl tbody tr')).toHaveLength(1)
  })
})

describe('activity.css — phone toolbar contract', () => {
  const css = readFileSync(resolve(__dirname, 'activity.css'), 'utf8')
  const phone = css.slice(css.indexOf('@media (max-width: 639.98px)'))

  it('uses the same breakpoint as useIsMobile so the JSX swap and the CSS collapse agree', () => {
    expect(css).toContain('@media (max-width: 639.98px)')
  })

  it('hides the chip row and shows the merged filter button only on a phone', () => {
    expect(css).toMatch(/\.act-type-filter \{\s*display: none;/)
    expect(phone).toMatch(/\.act-filter \{\s*display: none;/)
    expect(phone).toMatch(/\.act-type-filter \{\s*display: flex;/)
  })

  it('search comes first on the phone row and takes the leftover width', () => {
    expect(phone).toMatch(/\.act-search-c \{[^}]*order: -1;[^}]*flex: 1 1 auto;/s)
    expect(phone).toMatch(/\.act-filter-row \{[^}]*flex-wrap: nowrap;/s)
  })

  it('carries no phone override for the anchored range menu — the sheet replaced it', () => {
    expect(phone).not.toMatch(/\.act-dd-menu\s*\{/)
  })
})
