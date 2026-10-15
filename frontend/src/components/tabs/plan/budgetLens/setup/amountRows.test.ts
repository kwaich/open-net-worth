// Unit tests for the "which envelopes, and how much each" setup helpers behind Zero-Based's
// monthly targets. Pure functions, no DOM.
import { describe, expect, it } from 'vitest'
import {
  amountTotals,
  amountsPayload,
  initialAmountEdits,
  parseAmount,
  proposedAmountRows,
} from './amountRows'
import type { EnvelopeDraftRow } from '../types'

const ROWS: EnvelopeDraftRow[] = [
  {
    envelope_id: 'env_rent',
    label: 'Rent',
    low_confidence: false,
    suggested_amount: 2150,
    category_ids: ['cat_rent'],
  },
  {
    envelope_id: 'env_food',
    label: 'Food',
    low_confidence: false,
    suggested_amount: 600,
    category_ids: ['cat_g', 'cat_d'],
  },
  {
    envelope_id: 'env_new',
    label: 'New Thing',
    low_confidence: true,
    suggested_amount: 0,
    category_ids: [],
  },
]

describe('proposedAmountRows', () => {
  it('reads the engine’s own rows', () => {
    expect(proposedAmountRows({ rows: ROWS })).toHaveLength(3)
  })

  it('returns [] for anything unexpected rather than throwing — a setup screen must not white-screen on a surprising proposal', () => {
    expect(proposedAmountRows(null)).toEqual([])
    expect(proposedAmountRows({})).toEqual([])
    expect(proposedAmountRows({ rows: 'nope' } as never)).toEqual([])
    expect(proposedAmountRows({ rows: [{ nope: 1 }] } as never)).toEqual([])
  })
})

describe('initialAmountEdits', () => {
  it('a draft starts every proposed envelope included, at its suggested amount', () => {
    const edits = initialAmountEdits(ROWS, true)
    expect(edits.env_rent).toEqual({ included: true, amount: '2150' })
    expect(edits.env_food).toEqual({ included: true, amount: '600' })
  })

  it('a zero suggestion shows EMPTY, not a literal 0 the user must clear first', () => {
    expect(initialAmountEdits(ROWS, true).env_new).toEqual({ included: true, amount: '' })
  })

  it('"set up manually" is the same rows with nothing pre-picked, not a different screen', () => {
    const edits = initialAmountEdits(ROWS, false)
    expect(Object.keys(edits)).toHaveLength(3)
    expect(Object.values(edits).every((e) => !e.included)).toBe(true)
  })
})

describe('parseAmount', () => {
  it('never yields NaN — blank or garbage reads as 0 rather than reaching the API', () => {
    expect(parseAmount('')).toBe(0)
    expect(parseAmount('   ')).toBe(0)
    expect(parseAmount('lots')).toBe(0)
    expect(parseAmount('-5')).toBe(0)
  })

  it('rounds to cents', () => {
    expect(parseAmount('12.345')).toBe(12.35)
    expect(parseAmount('400')).toBe(400)
  })
})

describe('amountsPayload', () => {
  it('sends only included rows, in the order they were shown', () => {
    const edits = {
      ...initialAmountEdits(ROWS, true),
      env_food: { included: false, amount: '600' },
    }
    expect(amountsPayload(ROWS, edits)).toEqual([
      { envelope_id: 'env_rent', amount: 2150 },
      { envelope_id: 'env_new', amount: 0 },
    ])
  })

  it('an included row left blank sends 0, never NaN', () => {
    expect(amountsPayload(ROWS.slice(2), { env_new: { included: true, amount: '' } })).toEqual([
      { envelope_id: 'env_new', amount: 0 },
    ])
  })
})

describe('amountTotals', () => {
  it('counts and sums only what will actually be saved', () => {
    const edits = { ...initialAmountEdits(ROWS, true), env_new: { included: false, amount: '' } }
    expect(amountTotals(ROWS, edits)).toEqual({ count: 2, total: 2750, unfunded: 0 })
  })

  it('flags included rows with no amount — saving a target of nothing clears it', () => {
    expect(amountTotals(ROWS, initialAmountEdits(ROWS, true)).unfunded).toBe(1)
  })
})
