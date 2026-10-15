// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { Grant } from '../../../types/equity'

const refetch = vi.fn()
const useEquityView = vi.fn()
const authFetch = vi.fn()

vi.mock('./useEquityView', () => ({ useEquityView: () => useEquityView() }))
vi.mock('../../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...a) }))
vi.mock('../equity/EditGrantModal', () => ({
  EditGrantModal: ({ grant, onDone }: { grant: { symbol: string }; onDone: () => void }) => (
    <div>
      Edit modal for {grant.symbol}
      <button onClick={onDone}>Save edit</button>
    </div>
  ),
}))

import { EquityTab } from './EquityTab'

const rawGrant: Grant = {
  id: 'grant_1',
  type: 'rsu',
  instrument_id: 'inst_1',
  symbol: 'KSTL',
  grant_date: '2026-03-15',
  total_units: 1955,
  vested_units: 978,
  unvested_units: 977,
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
  tranches: [
    {
      id: 'tr_vested',
      vest_date: '2026-06-15',
      units: 489,
      kind: 'time',
      condition_note: null,
      vested: true,
      vest_event_id: 'vest_1',
      status: 'vested',
    },
    {
      id: 'tr_upcoming',
      vest_date: '2026-12-15',
      units: 489,
      kind: 'time',
      condition_note: null,
      vested: false,
      vest_event_id: null,
      status: 'upcoming',
    },
  ],
  note: '',
  created_at: '2026-03-15T00:00:00Z',
  updated_at: '2026-03-15T00:00:00Z',
}

function view() {
  return {
    loading: false,
    error: false,
    summaryCards: [],
    rawGrants: [rawGrant],
    upcoming: [],
    vestHistory: [],
    refetch,
    grants: [
      {
        id: 'grant_1',
        symbol: 'KSTL',
        grantDate: '2026-03-15',
        grantPrice: 20.36,
        currency: 'EUR',
        total: 1955,
        vested: 978,
        unvested: 977,
        nextVest: '2026-12-15',
        hasOverdue: false,
        taxEvent: 'at_vest',
        status: 'active',
        badgeLabel: 'Active',
        badgeTone: 'neutral',
        pct: 50,
        sinceGrantLine: null,
        tranches: [
          {
            id: 'tr_vested',
            date: '2026-06-15',
            units: 489,
            kind: 'time',
            vested: true,
            cls: 'done',
            badgeLabel: 'Vested',
            badgeTone: 'pos',
            sub: 'vested',
            overdue: false,
          },
          {
            id: 'tr_upcoming',
            date: '2026-12-15',
            units: 489,
            kind: 'time',
            vested: false,
            cls: 'next',
            badgeLabel: 'Upcoming',
            badgeTone: 'neutral',
            sub: 'in 84 days',
            overdue: false,
          },
        ],
      },
    ],
  }
}

function openDrawer() {
  render(<EquityTab />)
  fireEvent.click(screen.getByText('KSTL').closest('tr')!)
}

describe('EquityTab — undo a vested tranche', () => {
  beforeEach(() => {
    useEquityView.mockReturnValue(view())
    authFetch.mockReset()
  })
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  // Regression: the old handler POSTed to /api/equity/grants/{id}/tranches/{tid}/vest_undo —
  // a route that does not exist — with a swallowed catch, so the tap did nothing at all.
  it('confirms in a dialog, then POSTs to the real vest-events undo route and refetches', async () => {
    authFetch.mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    openDrawer()

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(screen.getByText('Undo this vest?')).toBeTruthy()
    expect(authFetch).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Undo vest' }))

    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith(
        '/api/equity/vest-events/vest_1/undo',
        expect.objectContaining({ method: 'POST' }),
      ),
    )
    await waitFor(() => expect(refetch).toHaveBeenCalled())
  })

  it('cancelling the dialog sends nothing', () => {
    openDrawer()
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(authFetch).not.toHaveBeenCalled()
    expect(screen.queryByText('Undo this vest?')).toBeNull()
  })

  it('surfaces a refusal on the row instead of swallowing it', async () => {
    authFetch.mockResolvedValue({ ok: false, status: 409, json: async () => ({}) })
    openDrawer()
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    fireEvent.click(screen.getByRole('button', { name: 'Undo vest' }))

    expect(await screen.findByText(/already been sold or disposed/)).toBeTruthy()
    expect(refetch).not.toHaveBeenCalled()
  })

  it('offers Edit grant beside Delete grant and refetches after a save', () => {
    openDrawer()
    fireEvent.click(screen.getByRole('button', { name: 'Edit grant' }))
    expect(screen.getByText('Edit modal for KSTL')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Save edit' }))
    expect(refetch).toHaveBeenCalledOnce()
  })
})
