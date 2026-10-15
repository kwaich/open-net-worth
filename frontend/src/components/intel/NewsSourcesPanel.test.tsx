// @vitest-environment jsdom

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { NewsSourcesPanel } from './NewsSourcesPanel'
import type { NewsSource } from './NewsSourceRow'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

// vi.clearAllMocks()/vi.restoreAllMocks() (rather than mockAuthFetch.mockClear()/.mockReset()
// directly) — the latter hangs vitest's next beforeEach when a prior test left a permanently-
// pending mock Promise attached to an unmounted component (the classic "Loading" state test
// below), the convention the (since-deleted) DedupDecisionsPanel test established for this exact case.
beforeEach(() => vi.clearAllMocks())
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function row(over: Partial<NewsSource> = {}): NewsSource {
  return {
    id: 'src_1',
    label: 'FT Companies',
    kind: 'outlet',
    transport: 'poll',
    url: 'https://ft.com/companies.rss',
    resolver: null,
    scope: 'firehose',
    match: 'server_tokens',
    cadence: 'hot',
    via: 'rss:ft_companies',
    enabled: true,
    min_interval_sec: 60,
    markets: [],
    provenance: 'builtin',
    notes: '',
    added_at: new Date(Date.now() - 5 * 24 * 3600 * 1000).toISOString(),
    latency: { n: 10, median_hours: 0.4, p75_hours: 0.7 },
    last_ok_at: new Date().toISOString(),
    failure_streak: null,
    ...over,
  }
}

function okJson(body: unknown, status = 200) {
  return { ok: true, status, json: async () => body }
}

describe('NewsSourcesPanel — states', () => {
  it('shows Loading initially', () => {
    mockAuthFetch.mockReturnValue(new Promise(() => {})) // never resolves
    const { getByText } = render(<NewsSourcesPanel embedded />)
    expect(getByText('Loading news sources…')).toBeTruthy()
  })

  it('shows the ErrorState + Retry on a failed fetch, and Retry re-fetches', async () => {
    mockAuthFetch.mockResolvedValueOnce({ ok: false, status: 500 })
    const { getByText } = render(<NewsSourcesPanel embedded />)
    await waitFor(() => expect(getByText('Could not load news sources.')).toBeTruthy())
    mockAuthFetch.mockResolvedValueOnce(okJson({ sources: [row()] }))
    fireEvent.click(getByText('Retry'))
    await waitFor(() => expect(getByText('Built-in sources • 1')).toBeTruthy())
  })

  it('shows the defensive EmptyState when the registry is genuinely empty', async () => {
    mockAuthFetch.mockResolvedValueOnce(okJson({ sources: [] }))
    const { getByText } = render(<NewsSourcesPanel embedded />)
    await waitFor(() => expect(getByText('No sources found')).toBeTruthy())
    expect(getByText(/Lucius couldn't find its source registry/)).toBeTruthy()
    expect(getByText('Retry')).toBeTruthy()
  })
})

describe('NewsSourcesPanel — embedded prop (default false)', () => {
  it('renders its own heading/subtitle when embedded is omitted (standalone reuse)', async () => {
    mockAuthFetch.mockResolvedValueOnce(okJson({ sources: [row()] }))
    const { getByText, container } = render(<NewsSourcesPanel />)
    await waitFor(() => expect(getByText('Built-in sources • 1')).toBeTruthy())
    expect(container.querySelector('.intel-mm-sources > .intel-mm-list-title')?.textContent).toBe(
      'News sources',
    )
  })

  it('renders WITHOUT its own heading when embedded (the one real MonitorsWorkspace caller)', async () => {
    mockAuthFetch.mockResolvedValueOnce(okJson({ sources: [row()] }))
    const { getByText, container } = render(<NewsSourcesPanel embedded />)
    await waitFor(() => expect(getByText('Built-in sources • 1')).toBeTruthy())
    expect(container.querySelector('.intel-mm-list-title')).toBeNull()
  })
})

describe('NewsSourcesPanel — two sections', () => {
  it('renders Built-in and Your-feeds dividers with live counts, and the empty-feeds line', async () => {
    mockAuthFetch.mockResolvedValueOnce(
      okJson({
        sources: [
          row({ id: 'src_1', provenance: 'builtin' }),
          row({ id: 'src_2', provenance: 'builtin' }),
        ],
      }),
    )
    const { getByText } = render(<NewsSourcesPanel embedded />)
    await waitFor(() => expect(getByText('Built-in sources • 2')).toBeTruthy())
    expect(getByText('Your feeds • 0')).toBeTruthy()
    expect(getByText('You haven’t added any feeds yet.')).toBeTruthy()
  })

  it('a user row never shows in the empty-feeds line and the count reflects it', async () => {
    mockAuthFetch.mockResolvedValueOnce(
      okJson({
        sources: [
          row({ id: 'src_1', provenance: 'builtin' }),
          row({ id: 'src_u1', provenance: 'user', label: 'My custom feed' }),
        ],
      }),
    )
    const { getByText, queryByText } = render(<NewsSourcesPanel embedded />)
    await waitFor(() => expect(getByText('Your feeds • 1')).toBeTruthy())
    expect(queryByText('You haven’t added any feeds yet.')).toBeFalsy()
    expect(getByText('My custom feed')).toBeTruthy()
  })
})

describe('NewsSourcesPanel — toggle + cadence PATCH', () => {
  it('toggling a switch optimistically flips then reverts + banners on failure', async () => {
    mockAuthFetch.mockResolvedValueOnce(okJson({ sources: [row({ enabled: true })] }))
    const { getByTitle, getByText } = render(<NewsSourcesPanel embedded />)
    await waitFor(() =>
      expect(getByTitle('Turn off — Lucius stops fetching this source right away.')).toBeTruthy(),
    )

    mockAuthFetch.mockResolvedValueOnce({ ok: false, status: 500 })
    const input = getByTitle(
      'Turn off — Lucius stops fetching this source right away.',
    ).querySelector('input') as HTMLInputElement
    fireEvent.click(input)
    // optimistic: title flips immediately to "turn on"
    await waitFor(() =>
      expect(
        getByTitle('Turn on — Lucius starts fetching this source on its next check.'),
      ).toBeTruthy(),
    )
    // revert + banner once the PATCH resolves as a failure
    await waitFor(() => expect(getByText('Could not update — try again.')).toBeTruthy())
    expect(getByTitle('Turn off — Lucius stops fetching this source right away.')).toBeTruthy()
  })

  it('a failed PATCH on one row does not stomp a concurrent optimistic edit on a different row (row-scoped revert)', async () => {
    const rowA = row({ id: 'src_1', label: 'Row A', enabled: true })
    const rowB = row({ id: 'src_2', label: 'Row B', enabled: true })
    mockAuthFetch.mockResolvedValueOnce(okJson({ sources: [rowA, rowB] }))
    const { getByRole } = render(<NewsSourcesPanel embedded />)
    const switchA = (await waitFor(() =>
      getByRole('switch', { name: 'Row A enabled' }),
    )) as HTMLInputElement
    const switchB = getByRole('switch', { name: 'Row B enabled' }) as HTMLInputElement

    // Deferred promises so both PATCHes are genuinely in flight at once, resolved in a
    // controlled order (B succeeds first, A fails second) — proves the fix is a functional,
    // per-id revert rather than a whole-array snapshot taken when the two edits started.
    let resolveA!: (v: unknown) => void
    let resolveB!: (v: unknown) => void
    const aPromise = new Promise((resolve) => {
      resolveA = resolve
    })
    const bPromise = new Promise((resolve) => {
      resolveB = resolve
    })
    mockAuthFetch.mockImplementation((url: string) => {
      if (url === '/api/intel/sources/src_1') return aPromise
      if (url === '/api/intel/sources/src_2') return bPromise
      return Promise.reject(new Error(`unexpected url ${url}`))
    })

    fireEvent.click(switchA) // optimistic: A -> false, PATCH A in flight
    fireEvent.click(switchB) // optimistic: B -> false, PATCH B in flight
    expect(switchA.checked).toBe(false)
    expect(switchB.checked).toBe(false)

    resolveB(okJson(row({ id: 'src_2', label: 'Row B', enabled: false })))
    await waitFor(() => expect(switchB.checked).toBe(false)) // B settles on its new value

    resolveA({ ok: false, status: 500 })
    await waitFor(() => expect(switchA.checked).toBe(true)) // A reverts to its pre-toggle value

    // The critical assertion: A's revert must NOT have stomped B back to checked.
    expect(switchB.checked).toBe(false)
  })

  it('a successful cadence PATCH updates the row from the server response — mock mirrors the REAL server refreshing min_interval_sec to the new cadence default', async () => {
    // Initial row: cadence=fast, min_interval_sec=300 (the fast default — see
    // services/news_sources.py's _CADENCE_DEFAULT_MIN_INTERVAL).
    mockAuthFetch.mockResolvedValueOnce(
      okJson({ sources: [row({ label: 'GDELT', cadence: 'fast', min_interval_sec: 300 })] }),
    )
    const { getByRole, getByText } = render(<NewsSourcesPanel embedded />)
    await waitFor(() => expect(getByRole('combobox', { name: 'Cadence for GDELT' })).toBeTruthy())
    expect(getByText('no more than every 5m')).toBeTruthy()

    // The client only ever sends {cadence} (asserted below) — the REAL backend
    // (services.news_sources.update_source_policy, review fix) refreshes min_interval_sec to
    // the hot default (60s) itself when it's not explicitly patched. This mock returns exactly
    // that server-computed row, not a value the frontend invented.
    mockAuthFetch.mockResolvedValueOnce(
      okJson(row({ label: 'GDELT', cadence: 'hot', min_interval_sec: 60 })),
    )
    fireEvent.change(getByRole('combobox', { name: 'Cadence for GDELT' }), {
      target: { value: 'hot' },
    })
    await waitFor(() => expect(getByText('no more than every 1m')).toBeTruthy())
    const call = mockAuthFetch.mock.calls[1] as [string, RequestInit]
    const [url, opts] = call
    expect(url).toBe('/api/intel/sources/src_1')
    expect(opts.method).toBe('PATCH')
    expect(JSON.parse(opts.body as string)).toEqual({ cadence: 'hot' })
  })
})

describe('NewsSourcesPanel — delete flow', () => {
  it('a user row shows the delete icon; confirming fires the DELETE and removes the row', async () => {
    mockAuthFetch.mockResolvedValueOnce(
      okJson({ sources: [row({ id: 'src_u9', provenance: 'user', label: 'My feed' })] }),
    )
    const { getByTitle, getByText, queryByText } = render(<NewsSourcesPanel embedded />)
    await waitFor(() => expect(getByText('My feed')).toBeTruthy())

    fireEvent.click(getByTitle('Remove this feed'))
    expect(getByText('Delete "My feed"?')).toBeTruthy()

    mockAuthFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true }) })
    fireEvent.click(getByText('Delete feed'))
    await waitFor(() => expect(queryByText('My feed')).toBeFalsy())
  })

  it('a builtin row never shows a delete icon', async () => {
    mockAuthFetch.mockResolvedValueOnce(okJson({ sources: [row({ provenance: 'builtin' })] }))
    const { getByText, queryByTitle } = render(<NewsSourcesPanel embedded />)
    await waitFor(() => expect(getByText('FT Companies')).toBeTruthy())
    expect(queryByTitle('Remove this feed')).toBeFalsy()
  })
})
