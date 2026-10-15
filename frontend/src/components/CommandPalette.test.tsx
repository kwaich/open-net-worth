// @vitest-environment jsdom
//
// I8 review regression: the keyboard-nav ACTIVE selection must be tracked by a stable
// command id, not a bare index into the navigable `flat` list. `flat` is rebuilt — and
// can reorder or shrink — whenever async Markets results land mid-navigation; a raw
// index then silently retargets onto whatever row now occupies that position, which
// can be an unrelated row or even the synthetic Ask Lucius row (Enter would open chat
// instead of running the row the user actually highlighted).
//
// Covers both ways `flat` changes shape once a market Tier-1 (contiguous) hit for the
// typed query lands:
//   1. The previously-active row's RANK shifts (Markets gets promoted ahead of it) —
//      the highlight must FOLLOW it to its new position.
//   2. The previously-active row is scatter-gated away entirely (no longer a Tier-1 hit
//      anywhere locally, per cmdkAssemble's scatter-suppression rule) — the highlight
//      must fall back to a fresh, sane default, never silently land on Ask Lucius.

import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react'
import { CommandPalette } from './CommandPalette'
import type { CommandPaletteProps } from './CommandPalette'
import type { ComputedRow } from '../types/portfolio'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../api/client', () => ({ authFetch }))

// jsdom doesn't implement scrollIntoView; the palette calls it when the user navigates
// with the keyboard (scroll-active-row-into-view effect).
Element.prototype.scrollIntoView = vi.fn()

function jsonResponse(data: unknown) {
  return Promise.resolve({ ok: true, json: async () => data })
}

// Renders the palette and flushes the deferred query/selection reset microtask that fires
// right after open (see the "Reset query + selection each time the palette opens" effect) —
// otherwise that reset can land AFTER a synchronous fireEvent.change in the same test tick
// and silently clobber the typed query back to ''.
async function renderPalette(props: CommandPaletteProps) {
  render(<CommandPalette {...props} />)
  await act(async () => {
    await Promise.resolve()
  })
}

function baseProps(): CommandPaletteProps {
  return {
    open: true,
    onClose: vi.fn(),
    onNavigate: vi.fn(),
    onOpenChat: vi.fn(),
    rows: [],
    onCategoryFilter: vi.fn(),
    onOpenAsset: vi.fn(),
  }
}

// The currently-active (aria-selected) option's visible label text, or null if none.
function activeOptionText(): string | null {
  const active = screen
    .getAllByRole('option')
    .find((el) => el.getAttribute('aria-selected') === 'true')
  return active?.textContent ?? null
}

// Presses ArrowDown (always at least once, so `userNavigated` — the flag that gates the
// bug's stale-index path — is genuinely set) until an option containing `label` becomes
// active, wrapping around the full navigable list if needed.
function arrowDownUntilActive(input: HTMLElement, label: string, max = 20) {
  for (let i = 0; i < max; i++) {
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    if (activeOptionText()?.includes(label)) return
  }
  throw new Error(`never landed on an option containing "${label}" within ${max} ArrowDown presses`)
}

const IREN_RESULT = {
  symbol: 'IREN',
  name: 'IREN Limited',
  asset_class: 'equity' as const,
  exchange: 'NASDAQ',
  price_source: 'yahoo' as const,
  price_source_id: 'IREN',
}

// Regression fixture (owner report 2026-08-10, "oklo" search): CoinGecko legitimately
// returns several DISTINCT tokenized-stock "wrapper" products sharing one ticker — the
// backend keeps them apart by (price_source, price_source_id), never by symbol alone.
const OKLO_WRAPPER_A = {
  symbol: 'OKLO',
  name: 'Oklo (Dinari Tokenized Stock)',
  asset_class: 'crypto' as const,
  exchange: '',
  price_source: 'coingecko' as const,
  price_source_id: 'oklo-dinari-tokenized-stock',
}
const OKLO_WRAPPER_B = {
  symbol: 'OKLO',
  name: 'Oklo Inc. • Robinhood Token',
  asset_class: 'crypto' as const,
  exchange: '',
  price_source: 'coingecko' as const,
  price_source_id: 'oklo-inc-robinhood-token',
}

beforeEach(() => {
  authFetch.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('CommandPalette keyboard-nav id stability (I8)', () => {
  it('keeps the same logical row highlighted when async market results reorder the list', async () => {
    let resolveMarkets!: (v: unknown) => void
    const marketsPromise = new Promise((resolve) => {
      resolveMarkets = resolve
    })
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/accounts')) {
        return jsonResponse([
          { id: 'a1', name: 'Siren Capital', type: 'investment', currency: 'USD' },
        ])
      }
      if (url.startsWith('/api/categories')) return jsonResponse([])
      if (url.startsWith('/api/intel/assets/search')) return marketsPromise
      return jsonResponse([])
    })

    await renderPalette(baseProps())
    const input = screen.getByRole('textbox', { name: /search or ask lucius/i })

    fireEvent.change(input, { target: { value: 'iren' } })
    // "Siren Capital" contains "iren" as a contiguous substring (Tier-1), so it's
    // always kept regardless of the scatter gate.
    await waitFor(() => expect(screen.getByText('Siren Capital')).toBeTruthy())

    // Navigate onto it explicitly via the keyboard.
    arrowDownUntilActive(input, 'Siren Capital')
    expect(activeOptionText()).toContain('Siren Capital')

    // Land the async market result: IREN is ALSO a Tier-1 local hit, and ranks better
    // (whole-word vs. Siren Capital's mid-word infix), so the Markets group gets
    // promoted ahead of Accounts — Siren Capital's flat INDEX shifts down even though
    // the row itself is untouched.
    resolveMarkets({ ok: true, json: async () => [IREN_RESULT] })
    await waitFor(() => expect(screen.getByText('IREN Limited')).toBeTruthy())

    // The highlight must have followed Siren Capital to its new position — not landed
    // on whatever now occupies its old index (the newly-promoted IREN market row).
    expect(activeOptionText()).toContain('Siren Capital')
  })

  it('falls back to a sane default — never Ask Lucius — when the active row is scatter-gated away', async () => {
    let resolveMarkets!: (v: unknown) => void
    const marketsPromise = new Promise((resolve) => {
      resolveMarkets = resolve
    })
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/accounts')) return jsonResponse([])
      if (url.startsWith('/api/categories')) return jsonResponse([])
      if (url.startsWith('/api/intel/assets/search')) return marketsPromise
      return jsonResponse([])
    })

    await renderPalette(baseProps())
    const input = screen.getByRole('textbox', { name: /search or ask lucius/i })

    fireEvent.change(input, { target: { value: 'iren' } })
    // "Dashboard" only SCATTER-matches "iren" via its hint text ("Net worth,
    // allocation, briefing" carries i/r/e/n) — kept pre-market because nothing
    // beats it (no Tier-1 hit exists yet anywhere).
    await waitFor(() => expect(screen.getByText('Dashboard')).toBeTruthy())

    arrowDownUntilActive(input, 'Dashboard')
    expect(activeOptionText()).toContain('Dashboard')

    // IREN lands as a Tier-1 local hit — the scatter gate flips and drops EVERY
    // scattered local match (Dashboard included), leaving only the market row.
    resolveMarkets({ ok: true, json: async () => [IREN_RESULT] })
    await waitFor(() => expect(screen.getByText('IREN Limited')).toBeTruthy())
    expect(screen.queryByText('Dashboard')).toBeNull()

    // Dashboard's id is gone from `flat` — the highlight must re-derive a fresh default
    // instead of landing on whatever now sits at its old raw index. A strong match
    // exists (the IREN row itself), so that default must NOT be Ask Lucius.
    const askLucius = screen.getByText(/Ask Lucius:/).closest('[role="option"]')
    expect(askLucius?.getAttribute('aria-selected')).toBe('false')
    expect(activeOptionText()).toContain('IREN Limited')
  })
})

describe('CommandPalette Markets — same-symbol, different-source results (2026-08-10 dupe bug)', () => {
  it('renders distinct same-symbol wrapper products as separate rows, not collapsed/duplicated', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/accounts')) return jsonResponse([])
      if (url.startsWith('/api/categories')) return jsonResponse([])
      if (url.startsWith('/api/intel/assets/search')) {
        return jsonResponse([OKLO_WRAPPER_A, OKLO_WRAPPER_B])
      }
      return jsonResponse([])
    })

    await renderPalette(baseProps())
    const input = screen.getByRole('textbox', { name: /search or ask lucius/i })
    fireEvent.change(input, { target: { value: 'oklo' } })

    await waitFor(() => expect(screen.getByText('Oklo Inc. • Robinhood Token')).toBeTruthy())

    // A symbol-only id (`mkt-OKLO`) collapses both into ONE Map entry that the group's
    // (unrelated) id list then references twice — rendering the SAME row 2x instead of
    // each result once. Assert both real, distinct labels are present, each exactly once.
    expect(screen.getAllByText('Oklo (Dinari Tokenized Stock)')).toHaveLength(1)
    expect(screen.getAllByText('Oklo Inc. • Robinhood Token')).toHaveLength(1)
    expect(
      screen.getAllByRole('option').filter((o) => o.textContent?.includes('OKLO')),
    ).toHaveLength(2)
  })
})

// Regression fixture (owner report 2026-08-11, "btc" search): a symbol you already hold
// used to be filtered OUT of Markets entirely (dedup vs Holdings) and Markets could still
// outrank Holdings on raw text-match quality — burying the one thing the user actually
// wanted (their own Bitcoin holding) under a page of obscure "BTC"-named securities.
const BTC_HOLDING_ROW = {
  label: 'Bitcoin',
  symbol: 'BTC',
  ticker: 'BTC',
  asset_class: 'crypto',
  position_id: 'pos-btc',
  instrument_id: 'inst-btc',
} as unknown as ComputedRow

const BTC_MARKET_RESULT = {
  symbol: 'BTC',
  name: 'Bitcoin',
  asset_class: 'crypto' as const,
  exchange: '',
  price_source: 'coingecko' as const,
  price_source_id: 'bitcoin',
}

describe('CommandPalette Markets — a held symbol is not deduped away (owner report, 2026-08-11)', () => {
  it('shows the held symbol in BOTH Holdings and Markets, with Holdings first', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/accounts')) return jsonResponse([])
      if (url.startsWith('/api/categories')) return jsonResponse([])
      if (url.startsWith('/api/intel/assets/search')) return jsonResponse([BTC_MARKET_RESULT])
      return jsonResponse([])
    })

    await renderPalette({ ...baseProps(), rows: [BTC_HOLDING_ROW] })
    const input = screen.getByRole('textbox', { name: /search or ask lucius/i })
    fireEvent.change(input, { target: { value: 'btc' } })

    await waitFor(() => expect(screen.getAllByText('Bitcoin').length).toBeGreaterThanOrEqual(2))

    const groupHeadings = [...document.querySelectorAll('.cmdk-group-label')].map(
      (el) => el.textContent,
    )
    const holdingsIdx = groupHeadings.indexOf('Holdings')
    const marketsIdx = groupHeadings.indexOf('Markets')
    expect(holdingsIdx).toBeGreaterThanOrEqual(0)
    expect(marketsIdx).toBeGreaterThan(holdingsIdx)
  })
})

// Regression (owner report 2026-09-16): typing "pltr" quickly, briefly overshooting to
// "pltrr" before backspacing, could leave the palette showing NO Markets result at all —
// not even the "Searching the market…" indicator. The stale-response guard compared
// `query` to itself (both read from the same closure captured when the "pltrr" fetch was
// scheduled), so it could never detect that the user had since corrected to "pltr". When
// the slower "pltrr" response (a typo, so it legitimately finds nothing) resolved AFTER
// the faster, correct "pltr" response, it silently overwrote the good result with an
// empty one and stamped `marketResultsQuery` with the now-stale "pltrr", which no longer
// matched the live query — so the freshness check dropped the Markets group entirely.
describe('CommandPalette Markets — a superseded slow response cannot clobber a fresher one (owner report, 2026-09-16)', () => {
  it('keeps the correct result after a stale, slower request for a corrected typo resolves late', async () => {
    let resolveStaleTypo!: (v: unknown) => void
    const staleTypoPromise = new Promise((resolve) => {
      resolveStaleTypo = resolve
    })
    const PLTR_RESULT = {
      symbol: 'PLTR',
      name: 'Palantir Technologies Inc.',
      asset_class: 'equity' as const,
      exchange: 'NASDAQ',
      price_source: 'yahoo' as const,
      price_source_id: 'PLTR',
    }

    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/accounts')) return jsonResponse([])
      if (url.startsWith('/api/categories')) return jsonResponse([])
      if (url.startsWith('/api/intel/assets/search')) {
        const q = new URL(url, 'http://test').searchParams.get('q')
        if (q === 'pltrr') return staleTypoPromise
        if (q === 'pltr') return jsonResponse([PLTR_RESULT])
        return jsonResponse([])
      }
      return jsonResponse([])
    })

    await renderPalette(baseProps())
    const input = screen.getByRole('textbox', { name: /search or ask lucius/i })

    // Overshoot with an extra "r", then correct it before the "pltrr" fetch resolves.
    fireEvent.change(input, { target: { value: 'pltrr' } })
    await waitFor(
      () =>
        expect(authFetch).toHaveBeenCalledWith(
          expect.stringContaining('q=pltrr'),
          expect.anything(),
        ),
      { timeout: 1000 },
    )

    fireEvent.change(input, { target: { value: 'pltr' } })
    await waitFor(() => expect(screen.getByText('Palantir Technologies Inc.')).toBeTruthy(), {
      timeout: 1000,
    })

    // The stale "pltrr" response (legitimately empty — it's a typo) finally arrives late.
    resolveStaleTypo({ ok: true, json: async () => [] })

    // It must not clobber the fresher, correct "pltr" result.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.getByText('Palantir Technologies Inc.')).toBeTruthy()
  })
})

describe('CommandPalette mobile close affordance', () => {
  it('renders a named Cancel control that closes the palette', async () => {
    authFetch.mockImplementation(() => jsonResponse([]))
    const props = baseProps()

    await renderPalette(props)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel search' }))

    expect(props.onClose).toHaveBeenCalledTimes(1)
  })
})

describe('CommandPalette tab destinations', () => {
  it('can search to the new Lucius tab', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/accounts')) return jsonResponse([])
      if (url.startsWith('/api/categories')) return jsonResponse([])
      return jsonResponse([])
    })

    await renderPalette(baseProps())
    const input = screen.getByRole('textbox', { name: /search or ask lucius/i })

    fireEvent.change(input, { target: { value: 'lucius' } })
    await waitFor(() => expect(screen.getByText('Lucius')).toBeTruthy())
  })
})
