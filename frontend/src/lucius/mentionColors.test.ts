// mentionColors.test.ts — colorFor precedence + per-type defaults.
import { describe, it, expect } from 'vitest'
import { C, SEMANTIC, TYPE_COLOR, colorFor } from './mentionColors'

// ── Palette completeness ──────────────────────────────────────────────────────

describe('C palette', () => {
  it('has all required named colors', () => {
    expect(C.cash).toBe('#34b08a')
    expect(C.invest).toBe('#3b86dd')
    expect(C.lucius).toBe('var(--lucius)')
    expect(C.pos).toBe('var(--good)')
    expect(C.neg).toBe('var(--bad)')
  })
})

// ── Precedence rules ──────────────────────────────────────────────────────────

describe('colorFor — precedence', () => {
  it('step 1: item.dot wins over tone and type', () => {
    const result = colorFor({ dot: '#aabbcc', tone: 'lucius', type: 'account' })
    expect(result).toBe('#aabbcc')
  })

  it('step 2: SEMANTIC[item.tone] wins when dot is absent', () => {
    const result = colorFor({ tone: 'crypto', type: 'account' })
    expect(result).toBe(SEMANTIC.crypto)
  })

  it('step 3: TYPE_COLOR[item.type] wins when dot and tone are absent', () => {
    const result = colorFor({ type: 'holding' })
    expect(result).toBe(TYPE_COLOR.holding)
    expect(result).toBe(C.invest)
  })

  it('step 4: neutral fallback when type is unknown and no dot/tone', () => {
    const result = colorFor({ type: 'totally_unknown_future_type' })
    expect(result).toBe(SEMANTIC.neutral)
  })

  it('null item → neutral', () => {
    expect(colorFor(null)).toBe(SEMANTIC.neutral)
  })

  it('undefined item → neutral', () => {
    expect(colorFor(undefined)).toBe(SEMANTIC.neutral)
  })

  it('empty object → neutral (no type/dot/tone)', () => {
    expect(colorFor({})).toBe(SEMANTIC.neutral)
  })
})

// ── Per-type defaults ─────────────────────────────────────────────────────────

describe('colorFor — type defaults', () => {
  it('dotless holding → invest hex (C.invest)', () => {
    expect(colorFor({ type: 'holding' })).toBe(C.invest)
  })

  it('instrument → invest (same as holding)', () => {
    expect(colorFor({ type: 'instrument' })).toBe(C.invest)
  })

  it('net_worth → lucius', () => {
    expect(colorFor({ type: 'net_worth' })).toBe(C.lucius)
  })

  it('target → retire (same color as cash/retire)', () => {
    expect(colorFor({ type: 'target' })).toBe(C.retire)
  })

  it('allocation_slice → invest', () => {
    expect(colorFor({ type: 'allocation_slice' })).toBe(C.invest)
  })

  it('returns (handoff "time") → neutral', () => {
    expect(colorFor({ type: 'returns' })).toBe(SEMANTIC.neutral)
  })

  it('budget_month (handoff "budget") → credit', () => {
    expect(colorFor({ type: 'budget_month' })).toBe(C.credit)
  })

  it('scheduled_task (handoff "scheduled") → credit', () => {
    expect(colorFor({ type: 'scheduled_task' })).toBe(C.credit)
  })

  it('skill → lucius', () => {
    expect(colorFor({ type: 'skill' })).toBe(C.lucius)
  })

  it('screen → lucius', () => {
    expect(colorFor({ type: 'screen' })).toBe(C.lucius)
  })

  it('source → lucius', () => {
    expect(colorFor({ type: 'source' })).toBe(C.lucius)
  })

  it('account → cash', () => {
    expect(colorFor({ type: 'account' })).toBe(C.cash)
  })

  it('rule → #646c7c', () => {
    expect(colorFor({ type: 'rule' })).toBe('#646c7c')
  })
})

// ── Transaction direction tint (routed through dot) ───────────────────────────

describe('colorFor — transaction direction', () => {
  it('transaction with dot=C.neg (outflow) → var(--bad)', () => {
    expect(colorFor({ type: 'transaction', dot: C.neg })).toBe('var(--bad)')
  })

  it('transaction with dot=C.pos (inflow) → var(--good)', () => {
    expect(colorFor({ type: 'transaction', dot: C.pos })).toBe('var(--good)')
  })

  it('transaction with no dot (direction unclear) → neutral, not green', () => {
    expect(colorFor({ type: 'transaction' })).toBe(SEMANTIC.neutral)
    expect(TYPE_COLOR.transaction).toBe(SEMANTIC.neutral)
  })
})

// ── Account color (routed through dot) ───────────────────────────────────────

describe('colorFor — account with explicit color', () => {
  it('account with dot set → returns that dot value', () => {
    const result = colorFor({ type: 'account', dot: '#9a7fd0' })
    expect(result).toBe('#9a7fd0')
  })

  it('account without dot → TYPE_COLOR.account default', () => {
    expect(colorFor({ type: 'account' })).toBe(C.cash)
  })
})

// ── Unknown type never colorless ─────────────────────────────────────────────

describe('colorFor — never colorless', () => {
  it('completely unknown type returns neutral, not undefined or empty', () => {
    const result = colorFor({ type: '__plugin_type_xyz__' })
    expect(result).toBeTruthy()
    expect(result).toBe(SEMANTIC.neutral)
  })
})
