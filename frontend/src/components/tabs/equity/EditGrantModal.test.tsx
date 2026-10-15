// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, cleanup, fireEvent } from '@testing-library/react'
import type { Grant } from '../../../types/equity'

const authFetch = vi.fn()
vi.mock('../../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))

const fetchJson = vi.fn()
vi.mock('../../../hooks/serverState', () => ({
  ACCOUNTS_KEY: 'accounts',
  fetchJson: (...a: unknown[]) => fetchJson(...a),
}))
vi.mock('../../../state/queryCache', () => ({
  fetchCachedQuery: (_key: string, fn: () => Promise<unknown>) => fn(),
}))

import { EditGrantModal } from './EditGrantModal'

function grant(over: Partial<Grant> = {}): Grant {
  return {
    id: 'grant_1',
    type: 'rsu',
    instrument_id: 'inst_1',
    symbol: 'KSTL',
    grant_date: '2026-03-15',
    total_units: 1955,
    vested_units: 978,
    unvested_units: 978,
    next_vest_date: '2026-12-15',
    currency: 'EUR',
    grant_price_per_unit: 20.36,
    tax_event: 'at_vest',
    status: 'active',
    release_required: false,
    pending_release_units: 0,
    locked_position_id: 'pos_1',
    pending_position_id: null,
    account_id: 'acct_1',
    tranches: [],
    note: '',
    created_at: '2026-03-15T00:00:00Z',
    updated_at: '2026-03-15T00:00:00Z',
    ...over,
  }
}

describe('EditGrantModal', () => {
  beforeEach(() => {
    authFetch.mockReset()
    fetchJson.mockReset()
    fetchJson.mockResolvedValue([{ id: 'acct_1', name: 'JP Morgan', kind: 'asset' }])
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('prefills every field from the grant being edited', async () => {
    render(<EditGrantModal grant={grant()} onClose={() => {}} onDone={() => {}} />)
    expect(await screen.findByDisplayValue('2026-03-15')).toBeTruthy()
    expect(screen.getByDisplayValue('20.36')).toBeTruthy()
    expect((screen.getByLabelText(/Tax event/) as HTMLSelectElement).value).toBe('at_vest')
    expect((screen.getByLabelText(/Share delivery/) as HTMLSelectElement).value).toBe('at_vest')
  })

  it('PATCHes only the changed release_required flag, leaving other fields as-is', async () => {
    authFetch.mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    const onDone = vi.fn()
    render(<EditGrantModal grant={grant()} onClose={() => {}} onDone={onDone} />)

    await screen.findByDisplayValue('2026-03-15')
    fireEvent.change(screen.getByLabelText(/Share delivery/), { target: { value: 'delayed' } })
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }))

    await waitFor(() => expect(authFetch).toHaveBeenCalled())
    const [url, init] = authFetch.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/equity/grants/grant_1')
    expect(init.method).toBe('PATCH')
    const body = JSON.parse(init.body as string)
    expect(body.release_required).toBe(true)
    expect(body.grant_date).toBe('2026-03-15')
    expect(body.grant_price_per_unit).toBe(20.36)
    expect(body).not.toHaveProperty('account_id')
    await waitFor(() => expect(onDone).toHaveBeenCalled())
  })

  it('surfaces the server error instead of closing on failure', async () => {
    authFetch.mockResolvedValue({
      ok: false,
      status: 404,
      json: async () => ({ detail: 'grant not found' }),
    })
    const onDone = vi.fn()
    render(<EditGrantModal grant={grant()} onClose={() => {}} onDone={onDone} />)

    await screen.findByDisplayValue('2026-03-15')
    fireEvent.click(screen.getByRole('button', { name: /Save changes/ }))

    expect(await screen.findByText(/grant not found/)).toBeTruthy()
    expect(onDone).not.toHaveBeenCalled()
  })
})
