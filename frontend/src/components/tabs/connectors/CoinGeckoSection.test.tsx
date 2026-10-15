// @vitest-environment jsdom
//
// Security review fix (2026-08-22): a saved CoinGecko API key could never be cleared, only
// overwritten with another non-empty key — CoinGecko's Remove action refuses outright (it is
// not `pausable`), and a blank field never registered as "dirty" (see
// `formSave.unrevealed`), so there was no way to revoke a leaked/compromised key at all. This
// exercises the "Clear saved key" action added to fix that: it must actually arm the footer
// Save, send an EXPLICIT empty string (never an omitted field, which the store reads as
// "leave unchanged" — `market_data_config_store.MarketDataConfigStore.update`), and the UI
// must reflect no key saved afterward.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react'
import { useConnectorSaveHost, type ConnectorSaveHost } from './formSave'
import { CoinGeckoSection } from './CoinGeckoSection'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

const CONFIG_URL = '/api/market-data/config'
const STATUS_URL = '/api/market-data/status'

const CONFIG_WITH_KEY = {
  has_coingecko_key: true,
  coingecko_api_key_tier: 'demo' as const,
  yahoo_primary_for_major_crypto: true,
}

const CONFIG_NO_KEY = {
  has_coingecko_key: false,
  coingecko_api_key_tier: 'demo' as const,
  yahoo_primary_for_major_crypto: true,
}

const STATUS = {
  last_ok_at: null,
  failure_streak: 0,
  cooldown_until: null,
  calls_last_minute: 0,
  yahoo: { last_ok_at: null, failure_streak: 0, cooldown_until: null },
}

function putBodiesTo(url: string): Record<string, unknown>[] {
  return authFetch.mock.calls
    .filter((c) => c[0] === url && (c[1] as RequestInit | undefined)?.method === 'PUT')
    .map((c) => JSON.parse((c[1] as RequestInit).body as string) as Record<string, unknown>)
}

/** Hosts the section exactly like ConnectorDetail's modal does — the footer's Save calls
 *  `host.run()`, which invokes whatever the form last published. */
function Harness({ hostRef }: { hostRef: { current: ConnectorSaveHost | null } }) {
  const host = useConnectorSaveHost()
  hostRef.current = host
  return (
    <host.Provider>
      <CoinGeckoSection />
    </host.Provider>
  )
}

function mockFetch(config: typeof CONFIG_WITH_KEY, putResult: typeof CONFIG_WITH_KEY = config) {
  let current = config
  authFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === CONFIG_URL && !init?.method) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(current) })
    }
    if (url === STATUS_URL && !init?.method) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(STATUS) })
    }
    if (url === CONFIG_URL && init?.method === 'PUT') {
      current = putResult
      return Promise.resolve({ ok: true, json: () => Promise.resolve(putResult) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

describe('CoinGeckoSection — clearing a saved key', () => {
  it('offers no clear action when no key is saved', async () => {
    mockFetch(CONFIG_NO_KEY)
    render(<CoinGeckoSection />)
    await screen.findByLabelText('API key')

    expect(screen.queryByRole('button', { name: /clear saved key/i })).toBeNull()
  })

  it('arms the footer Save and explains the pending clear, without touching the field via typing', async () => {
    mockFetch(CONFIG_WITH_KEY)
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await screen.findByLabelText('API key')

    expect(hostRef.current?.state?.dirty).toBeFalsy()

    fireEvent.click(screen.getByRole('button', { name: /clear saved key/i }))

    expect(await screen.findByText(/saved key will be removed when you save/i)).toBeTruthy()
    await waitFor(() => expect(hostRef.current?.state?.dirty).toBe(true))
  })

  it('sends an EXPLICIT null on save, and reflects no key saved afterward', async () => {
    mockFetch(CONFIG_WITH_KEY, CONFIG_NO_KEY)
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await screen.findByLabelText('API key')

    fireEvent.click(screen.getByRole('button', { name: /clear saved key/i }))
    await waitFor(() => expect(hostRef.current?.state?.dirty).toBe(true))

    await act(async () => {
      await hostRef.current!.run()
    })

    const bodies = putBodiesTo(CONFIG_URL)
    expect(bodies).toHaveLength(1)
    expect(bodies[0]?.coingecko_api_key).toBeNull()

    expect(await screen.findByPlaceholderText(/Paste your Demo or Pro API key/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /clear saved key/i })).toBeNull()
  })

  it('an ordinary save with an untouched field omits the key entirely (keeps it saved)', async () => {
    mockFetch(CONFIG_WITH_KEY)
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    await screen.findByLabelText('API key')

    // Change the tier only — never touch the key field or the clear action.
    fireEvent.click(screen.getByRole('tab', { name: 'Pro' }))
    await waitFor(() => expect(hostRef.current?.state?.dirty).toBe(true))

    await act(async () => {
      await hostRef.current!.run()
    })

    const bodies = putBodiesTo(CONFIG_URL)
    expect(bodies).toHaveLength(1)
    expect(bodies[0]?.coingecko_api_key).toBeUndefined()
    expect(bodies[0]?.coingecko_api_key_tier).toBe('pro')
  })

  it('typing a new key after clicking Clear overrides the clear', async () => {
    mockFetch(CONFIG_WITH_KEY)
    const hostRef: { current: ConnectorSaveHost | null } = { current: null }
    render(<Harness hostRef={hostRef} />)
    const field = await screen.findByLabelText('API key')

    fireEvent.click(screen.getByRole('button', { name: /clear saved key/i }))
    await screen.findByText(/saved key will be removed when you save/i)

    fireEvent.change(field, { target: { value: 'new-real-key' } })
    expect(screen.queryByText(/saved key will be removed when you save/i)).toBeNull()

    await waitFor(() => expect(hostRef.current?.state?.dirty).toBe(true))
    await act(async () => {
      await hostRef.current!.run()
    })

    const bodies = putBodiesTo(CONFIG_URL)
    expect(bodies[0]?.coingecko_api_key).toBe('new-real-key')
  })
})
