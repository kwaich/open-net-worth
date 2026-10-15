// A buy/sell cash leg names its trade in the register — "Sold 100 KSTL @ €37.45" — from the
// row's stored ticker + quantity and the price read from its lot/disposal (found 2026-10-15:
// every IBKR sale read as a bare "Sale proceeds").

import { describe, it, expect } from 'vitest'
import { tradeLabel } from './txnView'
import { fromApiTxn } from './data'
import type { ApiTxn, Txn } from './types'

const leg = (over: Partial<Txn>): Txn =>
  ({ kind: 'investment', ticker: 'KSTL', quantity: 100, ...over }) as Txn

describe('tradeLabel', () => {
  it('names a sale with its quantity and price', () => {
    const t = leg({ unitPrice: 37.45, unitPriceCurrency: 'EUR' })
    expect(tradeLabel(t, true)).toBe('Sold 100 KSTL @ €37.45')
  })

  it('names a buy, and leaves out a price it does not know', () => {
    expect(tradeLabel(leg({ ticker: 'VOO', quantity: 1.5 }), false)).toBe('Bought 1.5 VOO')
  })

  it('is null for a non-investment row or an investment row with no ticker', () => {
    expect(tradeLabel(leg({ kind: 'expense' }), false)).toBeNull()
    expect(tradeLabel(leg({ ticker: null }), true)).toBeNull()
  })

  it('carries the server price through the API mapping', () => {
    const api = {
      id: 't',
      date: '2026-10-14',
      kind: 'investment',
      unit_price: 37.45,
      unit_price_currency: 'EUR',
      base_amount: 0,
      cleared: false,
    } as unknown as ApiTxn
    const t = fromApiTxn(api)
    expect([t.unitPrice, t.unitPriceCurrency]).toEqual([37.45, 'EUR'])
  })
})
