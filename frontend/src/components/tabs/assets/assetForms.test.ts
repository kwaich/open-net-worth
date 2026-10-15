// Values and validation behind the create steps and the section editors built from them.
import { describe, expect, it } from 'vitest'
import type { OwnedAsset } from '../../../types/assets'
import {
  EMPTY_OWNERSHIP,
  NEW_LOAN,
  nameValueErrors,
  newLoanErrors,
  ownershipErrors,
  ownershipFromAsset,
  purchaseErrors,
  purchaseFromAsset,
  withOwnership,
  withPurchase,
} from './assetForms'

const TODAY = '2026-09-26'

describe('nameValueErrors', () => {
  it('asks for a name and for what it is worth today', () => {
    expect(nameValueErrors('  ', null)).toEqual({
      name: 'Enter a name.',
      value: "Enter what it's worth today.",
    })
  })

  it('refuses a value of 0 or less', () => {
    expect(nameValueErrors('Rolex', 0)).toEqual({ value: 'Enter a value greater than 0.' })
  })

  it('passes a name and a positive value', () => {
    expect(nameValueErrors('Rolex', 14800)).toEqual({})
  })
})

describe('ownershipErrors', () => {
  it('accepts all-mine whatever share was typed before switching back', () => {
    expect(ownershipErrors({ ...EMPTY_OWNERSHIP, shared: false, sharePct: 150 })).toEqual({})
  })

  it('refuses a shared share outside 0–100', () => {
    for (const sharePct of [150, -1, null]) {
      expect(ownershipErrors({ ...EMPTY_OWNERSHIP, shared: true, sharePct })).toEqual({
        share_pct: 'Enter a share from 0 to 100.',
      })
    }
    expect(ownershipErrors({ ...EMPTY_OWNERSHIP, shared: true, sharePct: 0 })).toEqual({})
    expect(ownershipErrors({ ...EMPTY_OWNERSHIP, shared: true, sharePct: 100 })).toEqual({})
  })
})

describe('purchaseErrors', () => {
  it('leaves both fields optional', () => {
    expect(purchaseErrors({ cost: null, costDate: '' }, TODAY)).toEqual({})
  })

  it('refuses a price of 0 or less and a date after today', () => {
    expect(purchaseErrors({ cost: 0, costDate: '2026-09-27' }, TODAY)).toEqual({
      cost: 'Enter a value greater than 0, or leave it blank.',
      cost_date: "Pick a date that isn't in the future.",
    })
    expect(purchaseErrors({ cost: 9000, costDate: TODAY }, TODAY)).toEqual({})
  })
})

describe('newLoanErrors', () => {
  it('asks the inline loan for a name and an amount owed above 0', () => {
    expect(newLoanErrors(' ', null)).toEqual({
      name: 'Enter a name for this loan.',
      owed: 'Enter an amount greater than 0.',
    })
    expect(newLoanErrors('Car loan', 0)).toEqual({ owed: 'Enter an amount greater than 0.' })
    expect(newLoanErrors('Car loan', 38000)).toEqual({})
  })
})

describe("merging a step's change into the newest value", () => {
  it('applies only the fields a patch names', () => {
    const v = { ...EMPTY_OWNERSHIP, shared: true, sharePct: 60, shareNote: 'Joint' }
    expect(withOwnership(v, { shareNote: 'Joint with A. Lim' })).toEqual({
      ...v,
      shareNote: 'Joint with A. Lim',
    })
    expect(withPurchase({ cost: 9000, costDate: '' }, { costDate: '2019-08-01' })).toEqual({
      cost: 9000,
      costDate: '2019-08-01',
    })
  })

  it('starts a share at 50 the moment Shared is picked, and keeps one already typed', () => {
    expect(withOwnership(EMPTY_OWNERSHIP, { shared: true }).sharePct).toBe(50)
    const typed = { ...EMPTY_OWNERSHIP, shared: true, sharePct: 60 }
    expect(withOwnership(typed, { shared: false })).toEqual({ ...typed, shared: false })
    expect(withOwnership({ ...typed, shared: false }, { shared: true }).sharePct).toBe(60)
  })
})

describe('seeding an editor from an asset', () => {
  const base = {
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    cost: null,
    cost_date: null,
  } as unknown as OwnedAsset

  it('reads a whole asset as all mine and a partial one as shared', () => {
    expect(ownershipFromAsset(base)).toEqual(EMPTY_OWNERSHIP)
    expect(
      ownershipFromAsset({
        ...base,
        share_pct: 50,
        share_note: 'Joint with A. Lim',
        loan_account_id: 'acc_1',
      }),
    ).toEqual({
      shared: true,
      sharePct: 50,
      shareNote: 'Joint with A. Lim',
      loanId: 'acc_1',
      newLoan: null,
    })
  })

  it('reads the purchase as the price and date, blank when missing', () => {
    expect(purchaseFromAsset(base)).toEqual({ cost: null, costDate: '' })
    expect(purchaseFromAsset({ ...base, cost: 9000, cost_date: '2019-08-01' })).toEqual({
      cost: 9000,
      costDate: '2019-08-01',
    })
  })

  it('never uses an account id as the staged-loan marker', () => {
    expect(NEW_LOAN).not.toMatch(/^acc_/)
    expect(NEW_LOAN).not.toBe('')
  })
})
