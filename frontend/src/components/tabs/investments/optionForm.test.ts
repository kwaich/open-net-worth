import { describe, it, expect } from 'vitest'
import {
  STRATEGY_PRESETS,
  deriveFromPreset,
  totalPremium,
  cspCollateral,
  validateDraft,
  draftToPayload,
  type OpenOptionDraft,
} from './optionForm'

function draft(overrides: Partial<OpenOptionDraft> = {}): OpenOptionDraft {
  return {
    underlying_instrument_id: 'inst_1',
    account_id: 'acc_1',
    option_type: 'call',
    side: 'short',
    strike: '160',
    expiry: '2026-09-18',
    contracts: '2',
    multiplier: '100',
    premium_per_share: '2.5',
    premium_currency: 'USD',
    open_date: '2026-07-12',
    strategy_tag: 'covered_call',
    premium_account_id: '',
    note: '',
    ...overrides,
  }
}

describe('deriveFromPreset', () => {
  it('covers all six presets', () => {
    expect(STRATEGY_PRESETS).toHaveLength(6)
    expect(deriveFromPreset('covered_call')).toEqual({
      option_type: 'call',
      side: 'short',
      strategy_tag: 'covered_call',
    })
    expect(deriveFromPreset('csp')).toEqual({
      option_type: 'put',
      side: 'short',
      strategy_tag: 'csp',
    })
    expect(deriveFromPreset('long_call')).toEqual({
      option_type: 'call',
      side: 'long',
      strategy_tag: 'long_call',
    })
    expect(deriveFromPreset('long_put')).toEqual({
      option_type: 'put',
      side: 'long',
      strategy_tag: 'long_put',
    })
    expect(deriveFromPreset('protective_put')).toEqual({
      option_type: 'put',
      side: 'long',
      strategy_tag: 'protective_put',
    })
    expect(deriveFromPreset('custom')).toBeNull()
  })
})

describe('premium & collateral math', () => {
  it('totalPremium follows the OCC per-share convention', () => {
    expect(totalPremium(1, 100, 2.5)).toBe(250)
    expect(totalPremium(2, 100, 1.25)).toBe(250)
    expect(totalPremium(0, 100, 5)).toBe(0)
  })
  it('cspCollateral = strike × contracts × multiplier', () => {
    expect(cspCollateral(140, 1, 100)).toBe(14000)
    expect(cspCollateral(140.5, 2, 100)).toBe(28100)
  })
})

describe('validateDraft', () => {
  it('accepts a complete draft', () => {
    expect(validateDraft(draft())).toBeNull()
  })
  it('requires underlying and account', () => {
    expect(validateDraft(draft({ underlying_instrument_id: '' }))).toMatch(/underlying/i)
    expect(validateDraft(draft({ account_id: '' }))).toMatch(/account/i)
  })
  it('rejects non-positive strike and contracts', () => {
    expect(validateDraft(draft({ strike: '0' }))).toMatch(/strike/i)
    expect(validateDraft(draft({ strike: 'abc' }))).toMatch(/strike/i)
    expect(validateDraft(draft({ contracts: '0' }))).toMatch(/contracts/i)
  })
  it('requires expiry and a whole positive multiplier', () => {
    expect(validateDraft(draft({ expiry: '' }))).toMatch(/expiry/i)
    expect(validateDraft(draft({ multiplier: '2.5' }))).toMatch(/multiplier/i)
  })
  it('rejects negative premium but allows zero', () => {
    expect(validateDraft(draft({ premium_per_share: '-1' }))).toMatch(/premium/i)
    expect(validateDraft(draft({ premium_per_share: '' }))).toBeNull()
  })
})

describe('draftToPayload', () => {
  it('coerces numerics and keeps required fields', () => {
    const p = draftToPayload(draft())
    expect(p.strike).toBe(160)
    expect(p.contracts).toBe(2)
    expect(p.multiplier).toBe(100)
    expect(p.premium_per_share).toBe(2.5)
    expect(p.premium_currency).toBe('USD')
  })
  it('omits empty optionals', () => {
    const p = draftToPayload(draft({ premium_account_id: '', note: '', strategy_tag: '' }))
    expect('premium_account_id' in p).toBe(false)
    expect('note' in p).toBe(false)
    expect('strategy_tag' in p).toBe(false)
    expect('allow_duplicate' in p).toBe(false)
  })
  it('passes allow_duplicate and wallet through when set', () => {
    const p = draftToPayload(draft({ allow_duplicate: true, premium_account_id: 'acc_cash' }))
    expect(p.allow_duplicate).toBe(true)
    expect(p.premium_account_id).toBe('acc_cash')
  })
})
