// @vitest-environment jsdom
//
// Unit tests for chipMetaFor — the shared chip decoration resolver.
// Covers: transaction (date+amount+direction), account (balance neutral),
// holding (value/marketValue neutral), unknown (empty output).

import { describe, it, expect, afterEach } from 'vitest'
import { chipMetaFor, resolveChipMeta, chipMetaFromDom } from './chipMeta'
import type { CtxFieldRecord } from './objectFields'

// ── transaction ────────────────────────────────────────────────────────────────

describe('chipMetaFor — transaction', () => {
  it('extracts chipDate (short formatted) and chipAmt with direction for an expense', () => {
    const rec: CtxFieldRecord = { date: '2026-06-18', amount: '−252.71 SGD' }
    const meta = chipMetaFor('transaction', rec)
    // Short date: Jun 18 (no year for current year)
    expect(meta.chipDate).toBeTruthy()
    expect(meta.chipDate).toContain('Jun')
    expect(meta.chipAmt).toBe('−252.71 SGD')
    expect(meta.chipAmtNeg).toBe(true)
  })

  it('sets chipAmtNeg=false for a positive (income) amount', () => {
    const rec: CtxFieldRecord = { date: '2026-05-01', amount: '+8000.00 SGD' }
    const meta = chipMetaFor('transaction', rec)
    expect(meta.chipAmt).toBe('+8000.00 SGD')
    expect(meta.chipAmtNeg).toBe(false)
  })

  it('accepts ASCII hyphen minus as a negative sign', () => {
    const rec: CtxFieldRecord = { date: '2026-01-10', amount: '-50.00 USD' }
    const meta = chipMetaFor('transaction', rec)
    expect(meta.chipAmtNeg).toBe(true)
  })

  it('returns empty object when record has no date or amount', () => {
    const rec: CtxFieldRecord = { kind: 'expense' }
    const meta = chipMetaFor('transaction', rec)
    expect(meta.chipDate).toBeUndefined()
    expect(meta.chipAmt).toBeUndefined()
    expect(meta.chipAmtNeg).toBeUndefined()
  })
})

// ── account ────────────────────────────────────────────────────────────────────

describe('chipMetaFor — account', () => {
  it('returns chipAmt from balance with no chipAmtNeg (neutral)', () => {
    const rec: CtxFieldRecord = { balance: 'S$12,340.00', name: 'Vantage Brokerage USD' }
    const meta = chipMetaFor('account', rec)
    expect(meta.chipAmt).toBe('S$12,340.00')
    expect(meta.chipAmtNeg).toBeUndefined()
    expect(meta.chipDate).toBeUndefined()
  })

  it('returns empty object when balance is absent', () => {
    const rec: CtxFieldRecord = { name: 'Empty Account' }
    const meta = chipMetaFor('account', rec)
    expect(meta.chipAmt).toBeUndefined()
  })
})

// ── holding ────────────────────────────────────────────────────────────────────
// All holding emitters use the canonical ctxFields.ts keys: `price` and `value`.
// The old `marketValue` alias is gone — AssetDetailView was migrated to use `value`.

describe('chipMetaFor — holding', () => {
  it('prefers "price" (per-share) over "value" (total) when both are present', () => {
    const rec: CtxFieldRecord = { price: 'S$185.20', value: 'S$48,200.00', name: 'AAPL' }
    const meta = chipMetaFor('holding', rec)
    expect(meta.chipAmt).toBe('S$185.20')
    expect(meta.chipAmtNeg).toBeUndefined()
  })

  it('returns chipAmt from "price" alone', () => {
    const rec: CtxFieldRecord = { price: 'S$185.20', name: 'AAPL' }
    const meta = chipMetaFor('holding', rec)
    expect(meta.chipAmt).toBe('S$185.20')
    expect(meta.chipAmtNeg).toBeUndefined()
  })

  it('returns chipAmt from "value" when price is absent', () => {
    const rec: CtxFieldRecord = { value: 'S$48,200.00', name: 'AAPL' }
    const meta = chipMetaFor('holding', rec)
    expect(meta.chipAmt).toBe('S$48,200.00')
    expect(meta.chipAmtNeg).toBeUndefined()
  })

  it('returns empty object when neither price nor value is present', () => {
    const rec: CtxFieldRecord = { name: 'AAPL' }
    const meta = chipMetaFor('holding', rec)
    expect(meta.chipAmt).toBeUndefined()
  })
})

// ── unknown type ───────────────────────────────────────────────────────────────

describe('chipMetaFor — unknown/other types', () => {
  it('returns empty object for "category"', () => {
    const rec: CtxFieldRecord = { name: 'Food', balance: 'S$500' }
    const meta = chipMetaFor('category', rec)
    expect(meta).toEqual({})
  })

  it('returns empty object for "payee"', () => {
    const rec: CtxFieldRecord = { name: 'Grab', balance: '100' }
    const meta = chipMetaFor('payee', rec)
    expect(meta).toEqual({})
  })

  it('returns empty object for an entirely unknown type', () => {
    const rec: CtxFieldRecord = { value: 'S$1000' }
    const meta = chipMetaFor('xyztype', rec)
    expect(meta).toEqual({})
  })
})

// ── resolveChipMeta — the unified resolver used by every chip surface ────────────
describe('resolveChipMeta', () => {
  it('on-screen transaction: amount comes from the NATIVE sublabel, not amount_base', () => {
    // Regression: post-1e the on-screen record carries amount_base (a base-currency
    // NUMBER for the model), so chipMetaFor(record) produced no chipAmt and the chip
    // showed only the date. The native figure shown on screen lives in the sublabel.
    const record: CtxFieldRecord = { date: '2026-06-25', amount_base: '-270' }
    const sublabel = 'Vantage Brokerage • Vantage Brokerage • 2026-06-25 • −S$270.00'
    const meta = resolveChipMeta('transaction', record, sublabel)
    expect(meta.chipDate).toBe('Jun 25')
    expect(meta.chipAmt).toBe('−S$270.00')
    expect(meta.chipAmtNeg).toBe(true)
  })

  it('off-screen transaction (no record): still reads the sublabel', () => {
    const meta = resolveChipMeta(
      'transaction',
      null,
      'Gojek • Meridian Bank • 2026-06-21 • +S$20.40',
    )
    expect(meta.chipDate).toBe('Jun 21')
    expect(meta.chipAmt).toBe('+S$20.40')
    expect(meta.chipAmtNeg).toBe(false)
  })

  it('transaction with neither record nor sublabel: empty', () => {
    expect(resolveChipMeta('transaction', null, null)).toEqual({})
  })

  it('non-transaction types delegate to chipMetaFor(record)', () => {
    const record: CtxFieldRecord = { balance: 'S$161,787.63' }
    expect(resolveChipMeta('account', record, null)).toEqual({ chipAmt: 'S$161,787.63' })
  })
})

// ── chipMetaFromDom — the (type, ref)-only resolver used by the ✦ Ask-Lucius button ──
describe('chipMetaFromDom', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('reads date+amount off the live DOM row (the ✦ button path)', () => {
    const el = document.createElement('div')
    el.setAttribute('data-entity-id', 'transaction:t-1')
    el.setAttribute('data-ctx-fields', JSON.stringify({ date: '2026-06-20', amount_base: '-20.4' }))
    el.setAttribute('data-entity-sublabel', 'Cabcharge • Meridian Bank • 2026-06-20 • −20.4 SGD')
    document.body.appendChild(el)

    const meta = chipMetaFromDom('transaction', 't-1')
    expect(meta.chipDate).toBe('Jun 20')
    expect(meta.chipAmt).toBe('−20.4 SGD')
    expect(meta.chipAmtNeg).toBe(true)
  })

  it('returns {} when the entity is not in the DOM (off-screen → label-only)', () => {
    expect(chipMetaFromDom('transaction', 'missing')).toEqual({})
  })
})
