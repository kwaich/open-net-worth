// @vitest-environment jsdom
//
// `cash`/`buying_power` are live account read-outs the backend still returns (never in scope
// for the connectors-as-data-sources stats/linked_accounts deletion, which only touched
// `positions_synced`) — a small Cluster of two Text items under the status line, hidden when
// null, formatted in USD (Alpaca is USD-only — services/aggregation/alpaca.py).

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { AlpacaSection } from './AlpacaSection'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(cleanup)

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

function mockStatus(overrides: Record<string, unknown> = {}) {
  mockAuthFetch.mockImplementation((url: string) => {
    if (url === '/api/alpaca/status') {
      return Promise.resolve(
        okJson({
          configured: true,
          enabled: true,
          environment: 'paper',
          has_keys: true,
          last_synced_at: null,
          last_status: 'ok',
          last_error: null,
          cash: null,
          buying_power: null,
          ...overrides,
        }),
      )
    }
    if (url === '/api/accounts') return Promise.resolve(okJson([]))
    // ConnectorAccountsList's own self-fetch — the real backend always includes `accounts`.
    if (url === '/api/connectors/alpaca/accounts') {
      return Promise.resolve(okJson({ status: 'ok', provider_label: 'Alpaca', accounts: [] }))
    }
    return Promise.resolve(okJson({}))
  })
}

describe('AlpacaSection — cash/buying_power readout', () => {
  it('shows both, formatted as USD, when present', async () => {
    mockStatus({ cash: 1234.5, buying_power: 5000 })
    render(<AlpacaSection />)
    expect(await screen.findByText(/Cash \$1,234\.50/)).toBeTruthy()
    expect(await screen.findByText(/Buying power \$5,000/)).toBeTruthy()
  })

  it('hides both when null', async () => {
    mockStatus({ cash: null, buying_power: null })
    render(<AlpacaSection />)
    await screen.findByLabelText(/^API key id/)
    expect(screen.queryByText(/^Cash /)).toBeNull()
    expect(screen.queryByText(/^Buying power /)).toBeNull()
  })

  it('shows only the one present when the other is null', async () => {
    mockStatus({ cash: 42, buying_power: null })
    render(<AlpacaSection />)
    expect(await screen.findByText(/Cash \$42/)).toBeTruthy()
    expect(screen.queryByText(/^Buying power /)).toBeNull()
  })
})
