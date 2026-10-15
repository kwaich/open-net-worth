// @vitest-environment jsdom
//
// Without the side panel (a phone), a card's payment detail floats as a modal — its explanation
// and Assign button included (owner, 2026-09-28).
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CardPaymentPanel } from './budgetCardPayment'
import type { LocalCat } from './useBudgetModel'

vi.mock('../../../api/client', () => ({
  authFetch: vi.fn(() => Promise.resolve({ ok: false, json: () => Promise.resolve(null) })),
}))

afterEach(cleanup)

const card = {
  key: 'env-cc',
  label: 'Visa',
  color: '#3b86dd',
  isPaymentEnvelope: true,
  assigned: 100,
  paymentOwed: 2000,
  availableForPayment: 800,
  paymentUnderfunded: 1200,
} as LocalCat

describe('CardPaymentPanel as a modal', () => {
  it('explains the shortfall and assigns it on top of what is assigned', () => {
    const onAssign = vi.fn()
    render(
      <CardPaymentPanel
        c={card}
        month="2026-10"
        note=""
        onSaveNote={vi.fn()}
        onBack={vi.fn()}
        onOpenAccounts={vi.fn()}
        onAssign={onAssign}
        inline={false}
      />,
    )
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText('Why is this?')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^Assign/ }))
    expect(onAssign).toHaveBeenCalledWith(1300)
  })
})
