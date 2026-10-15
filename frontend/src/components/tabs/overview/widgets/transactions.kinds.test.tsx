// @vitest-environment jsdom
//
// A transfer read as one bank's payee text in grey, and an investment leg as the broker's name
// in red or green — like spending or income. A transfer is named by its two accounts and never
// signed or coloured; an investment leg says what it bought or sold, its amount in the
// investment purple for both buys and sells, like the register.

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

import { transactionsWidgetComponents } from './transactions'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

describe('Transaction widget rows by kind', () => {
  it('names a transfer by its accounts and an investment leg by its trade', async () => {
    authFetch.mockImplementation(() =>
      jsonOk({
        transactions: [
          {
            id: 'tr',
            date: '2026-10-12',
            kind: 'transfer',
            description: "Payment to other bank's card ending 1234",
            from_account_id: 'a1',
            from_account_name: 'DBS Multiplier',
            to_account_id: 'a2',
            to_account_name: 'Citi Rewards',
            base_amount: 1543.13,
          },
          {
            id: 'buy',
            date: '2026-10-13',
            kind: 'investment',
            payee_name: 'IBKR',
            from_account_id: 'a3',
            from_account_name: 'IBKR',
            ticker: 'VOO',
            quantity: 2.1095,
            base_amount: 1283.38,
          },
          {
            id: 'sell',
            date: '2026-10-13',
            kind: 'investment',
            payee_name: 'IBKR',
            to_account_id: 'a3',
            to_account_name: 'IBKR',
            base_amount: 900,
          },
        ],
      }),
    )
    const Comp = transactionsWidgetComponents['recent-added-txns']!
    const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'recent-added-txns')!
    const instance: WidgetInstance = {
      instanceId: 'w-recent',
      defId: 'recent-added-txns',
      x: 0,
      y: 0,
      w: 4,
      h: 240,
      config: {},
    }
    render(<Comp instance={instance} def={def} onPatch={vi.fn()} />)

    const transfer = await screen.findByText('DBS Multiplier → Citi Rewards')
    const transferAmt = transfer
      .closest('.ov-upcoming-row')!
      .querySelector<HTMLElement>('.ov-upcoming-amt')!
    expect(transferAmt.style.color).toBe('var(--muted)')
    expect(transferAmt.textContent).not.toContain('-')

    const buy = screen.getByText(/Buy 2\.1095 VOO/)
    const buyAmt = buy.closest('.ov-upcoming-row')!.querySelector<HTMLElement>('.ov-upcoming-amt')!
    expect(buyAmt.style.color).toBe('var(--violet)')
    expect(buyAmt.textContent).toContain('-')

    // No account suffix ("· IBKR (USD)"): every other row shows none either.
    expect(buy.closest('.ov-upcoming-label')!.textContent).toBe('Buy 2.1095 VOO')

    // Buy and sell both read in the investment purple, like the register.
    const sell = screen.getByText('Sell · IBKR')
    const sellAmt = sell
      .closest('.ov-upcoming-row')!
      .querySelector<HTMLElement>('.ov-upcoming-amt')!
    expect(sellAmt.style.color).toBe('var(--violet)')
  })
})
