// Regression: the page used to gate its ENTIRE render on `loading || !coverage || !derived` —
// so as long as coverage/unmarked-payments hadn't resolved yet (a separate, slower fetch from
// the commitments list itself), the whole tab was replaced by one bare "Loading commitments…"
// spinner even though the commitments list had already loaded and could show something real.
// Owner: "even on the first load, please do not reload the entire page, only the chart, do
// not trigger the entire page loading which will be a bad UX." Fix: the page now renders as
// soon as the commitments list is ready; only the Masthead's own slot (which needs
// coverage/derived) shows a small scoped loading placeholder while that fetch is still in
// flight, and the commitment rows below it render immediately regardless.
// @vitest-environment jsdom
import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../../../state/queryCache'
import { Commitments } from './Commitments'
import type { Commitment } from '../../../types/commitments'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

function ok(data: unknown): Response {
  return { ok: true, json: async () => data } as Response
}

const commitment: Commitment = {
  id: 'c1',
  name: 'Parkview Residences',
  kind: 'purchase',
  currency: 'VND',
  status: 'active',
  linked_position_id: null,
  linked_account_id: null,
  note: '',
  created_at: '2026-01-01T00:00:00Z',
  installments: [],
  funding_plan: [],
  actions: [],
}

describe('Commitments page — masthead-scoped loading, not a whole-page reload', () => {
  beforeEach(() => {
    clearQueryCache()
    authFetch.mockReset()
  })
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('renders the commitment list as soon as it loads, even while coverage is still in flight', async () => {
    authFetch.mockImplementation((url: string) => {
      if (url === '/api/commitments') return Promise.resolve(ok([commitment]))
      // coverage + unmarked-payments never resolve during this test — simulates a slow first load
      return new Promise<Response>(() => {})
    })

    render(<Commitments fx={{ VND: 1 }} onAskLucius={() => {}} />)

    // The commitment row rendered — the page did NOT get stuck behind one bare full-page spinner.
    await waitFor(() =>
      expect(screen.queryAllByText('Parkview Residences').length).toBeGreaterThan(0),
    )

    // The full-page "Loading commitments…" state is gone; only the masthead's own slot is
    // showing a scoped placeholder while coverage/derived are still unresolved.
    expect(screen.queryByText('Loading commitments…')).toBeNull()
    expect(screen.queryByLabelText('Loading funding outlook')).toBeTruthy()
  })
})
