// @vitest-environment jsdom
//
// Payment-envelope Activity popover — the YNAB-reference two-column breakdown box (see the
// owner's screenshot: Spending/Returns/Total Spending vs Funded Spending/Payments & Returns/
// Total Activity) + the AMOUNT column showing each txn's CARD-ACCOUNT leg (payment +150,
// spend −20), never the envelope's own category-scoped `activity_amount`. An ordinary envelope
// fetch (no `isPaymentEnvelope`) must stay byte-identical: no breakdown box, `activity_amount`
// signs unchanged.
import { afterEach, describe, expect, it, vi, beforeEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
import { ActivityCell } from './budgetActivity'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

// Rows arrive labelled by the server (`services.labels`): the popover fetches no account or
// payee list of its own.
const PAYMENT_DETAIL = {
  transactions: [
    {
      id: 'pay1',
      kind: 'transfer',
      date: '2026-07-10',
      from_account_id: 'bank',
      from_account_name: 'Bank',
      to_account_id: 'amex',
      to_account_name: 'Meridian Bank WWDC',
      activity_amount: -150,
      card_leg_amount: 150,
      txn_type: 'payment',
      activity_memo: '',
    },
    {
      id: 'spend1',
      kind: 'expense',
      date: '2026-07-09',
      from_account_id: 'amex',
      from_account_name: 'Meridian Bank WWDC',
      category: 'cat_groceries',
      activity_amount: 20,
      card_leg_amount: -20,
      txn_type: 'spend',
      activity_memo: '',
    },
  ],
  excluded_count: 0,
  breakdown: {
    spending: -20,
    returns: 0,
    total_spending: -20,
    funded_spending: 20,
    payments_returns: -150,
    total_activity: -130,
  },
}

const ORDINARY_DETAIL = {
  transactions: [
    {
      id: 't1',
      kind: 'expense',
      date: '2026-07-09',
      from_account_id: 'bank',
      from_account_name: 'Bank',
      category: 'cat_groceries',
      activity_amount: -20,
      activity_memo: '',
    },
  ],
  excluded_count: 0,
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/planning/activity-transactions')) {
      return Promise.resolve(okJson(url.includes('env_cc') ? PAYMENT_DETAIL : ORDINARY_DETAIL))
    }
    return Promise.resolve(okJson({}))
  })
})
afterEach(cleanup)

describe('ActivityCell — payment-envelope breakdown box', () => {
  it('renders the two-column YNAB breakdown + card-leg-signed amounts', async () => {
    render(
      <ActivityCell
        c={{
          activity: -130,
          label: 'Meridian Bank WWDC',
          color: '#fff',
          envelopeId: 'env_cc',
          isPaymentEnvelope: true,
        }}
        month="2026-07"
      />,
    )
    fireEvent.click(screen.getByTitle('View transactions'))

    await waitFor(() => expect(screen.getByText('Total Activity')).toBeTruthy())
    expect(screen.getByText('Spending').nextSibling?.textContent).toBe('−S$20.00')
    expect(screen.getByText('Returns').nextSibling?.textContent).toBe('−S$0.00')
    expect(screen.getByText('Total Spending').nextSibling?.textContent).toBe('−S$20.00')
    expect(screen.getByText('Funded Spending').nextSibling?.textContent).toBe('+S$20.00')
    expect(screen.getByText('Payments & Returns').nextSibling?.textContent).toBe('−S$150.00')
    expect(screen.getByText('Total Activity').nextSibling?.textContent).toBe('−S$130.00')

    // AMOUNT column: the card's own leg, not the envelope's activity_amount contribution.
    expect(screen.getByText('+S$150.00')).toBeTruthy() // pay1's card_leg_amount
    expect(screen.getByText('−S$20.00', { selector: '.pl-actpop-row .r' })).toBeTruthy() // spend1's
  })

  it('an ordinary envelope fetch renders no breakdown box and keeps activity_amount signs', async () => {
    render(
      <ActivityCell
        c={{ activity: -20, label: 'Groceries', color: '#fff', categoryIds: ['cat_groceries'] }}
        month="2026-07"
      />,
    )
    fireEvent.click(screen.getByTitle('View transactions'))

    await waitFor(() => expect(screen.getByText('Bank')).toBeTruthy())
    expect(screen.queryByText('Funded Spending')).toBeNull()
    expect(document.querySelector('.pl-actpop-bd')).toBeNull()
    // getAll, not get: since fFig always prints cents (2026-09-15), the CELL and the popover's
    // own total render the identical string — two matches is correct, not ambiguous.
    expect(screen.getAllByText('−S$20.00').length).toBeGreaterThan(0) // activity_amount, unchanged
  })
})
