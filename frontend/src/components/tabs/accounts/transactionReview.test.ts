import { describe, expect, it } from 'vitest'
import {
  reviewDraftToUpdate,
  sourceLine,
  startingReviewDraft,
  summaryTitle,
  validateReviewDraft,
} from './transactionReview'
import type { Account, Txn } from './types'

// Deliberately partial fixtures (only what the review helpers read); a tuple so indexing is total.
const accounts = [
  { id: 'sgd', name: 'Checking', currency: 'SGD' },
  { id: 'usd', name: 'Brokerage', currency: 'USD' },
] as [Account, Account]

const expense: Txn = {
  id: 'txn-1',
  kind: 'expense',
  date: '2026-09-18',
  category: 'food',
  payeeId: null,
  payeeName: null,
  description: 'Market',
  memo: '',
  fromAccountId: 'sgd',
  fromAmount: 113.29,
  fromCurrency: 'SGD',
  toAccountId: null,
  toAmount: null,
  toCurrency: null,
  origAmount: 90,
  origCurrency: 'USD',
  baseAmount: 113.29,
  baseCurrency: 'SGD',
  splits: [],
  tags: [],
  cleared: false,
  source: 'manual',
  createdAt: '2026-09-18T03:00:00Z',
  deleted: false,
  needsReview: false,
  externalRefs: [],
}

describe('transaction review draft', () => {
  it('names the writer and the channel it wrote through, and an automation as the actor', () => {
    // A capture client (the iOS Shortcut) started a Lucius write: the audit's `via` is the
    // channel, so the card reads as the drawer's Provenance does.
    expect(sourceLine({ source: 'ai', via: 'iOS Shortcut', automationId: null } as Txn)).toBe(
      'Added by Lucius via iOS Shortcut',
    )
    expect(sourceLine({ source: 'ai', via: 'Telegram', automationId: null } as Txn)).toBe(
      'Added by Lucius via Telegram',
    )
    // A named automation IS the actor.
    expect(
      sourceLine({ source: 'automation', via: 'Bank SMS matcher', automationId: 'auto_1' } as Txn),
    ).toBe('Added by Bank SMS matcher')
    expect(sourceLine({ source: 'ai', via: null, automationId: null } as Txn)).toBe(
      'Added by Lucius',
    )
  })

  it('keeps account, kind, direction, and original charge in the editable draft', () => {
    expect(startingReviewDraft(expense, {})).toMatchObject({
      kind: 'expense',
      accountId: 'sgd',
      amount: 113.29,
      originalAmount: '90',
      originalCurrency: 'USD',
    })
  })

  it('approves an untouched row with a sparse review-only patch', () => {
    const draft = startingReviewDraft(expense, {})
    expect(reviewDraftToUpdate(expense, draft, accounts)).toEqual({ needs_review: false })
  })

  it('does not equalize an untouched same-currency transfer', () => {
    const transfer = {
      ...expense,
      kind: 'transfer',
      category: null,
      fromAmount: 100,
      toAccountId: 'usd-2',
      toAmount: 98,
      toCurrency: 'USD',
      origAmount: null,
      origCurrency: null,
    } as Txn
    const transferAccounts = [
      accounts[1],
      { ...accounts[1], id: 'usd-2', name: 'Savings' },
    ] as Account[]
    expect(
      reviewDraftToUpdate(transfer, startingReviewDraft(transfer, {}), transferAccounts),
    ).toEqual({ needs_review: false })
    expect(
      reviewDraftToUpdate(
        transfer,
        { ...startingReviewDraft(transfer, {}), amount: 101 },
        transferAccounts,
      ),
    ).toMatchObject({ from_amount: 101, to_amount: 98 })
  })

  it('rewrites both legs when an expense is corrected to income', () => {
    const draft = {
      ...startingReviewDraft(expense, {}),
      kind: 'income' as const,
      accountId: 'usd',
      amount: 120,
      category: null,
      originalAmount: '',
      originalCurrency: '',
    }
    expect(reviewDraftToUpdate(expense, draft, accounts)).toMatchObject({
      kind: 'income',
      from_account_id: null,
      from_amount: null,
      from_currency: null,
      to_account_id: 'usd',
      to_amount: 120,
      to_currency: 'USD',
      original_amount: null,
      original_currency: null,
    })
  })

  it('builds a complete FX transfer and rejects incomplete or duplicate legs', () => {
    const draft = {
      ...startingReviewDraft(expense, {}),
      kind: 'transfer' as const,
      accountId: 'sgd',
      toAccountId: 'usd',
      amount: 135,
      toAmount: 100,
      category: null,
    }
    expect(validateReviewDraft(expense, draft, accounts)).toBeNull()
    expect(reviewDraftToUpdate(expense, draft, accounts)).toMatchObject({
      kind: 'transfer',
      payee_id: null,
      from_account_id: 'sgd',
      from_amount: 135,
      from_currency: 'SGD',
      to_account_id: 'usd',
      to_amount: 100,
      to_currency: 'USD',
      original_amount: null,
      original_currency: null,
    })
    expect(validateReviewDraft(expense, { ...draft, toAccountId: 'sgd' }, accounts)).toMatch(
      /different accounts/i,
    )
  })

  it('does not select a newly chosen archived account or persist whitespace original metadata', () => {
    const archived = { ...accounts[1], archived: true } as Account
    const draft = {
      ...startingReviewDraft(expense, {}),
      accountId: 'usd',
      originalAmount: '   ',
      originalCurrency: '   ',
    }
    expect(validateReviewDraft(expense, draft, [accounts[0], archived])).toMatch(/active account/i)
    const update = reviewDraftToUpdate(expense, { ...draft, accountId: 'sgd' }, accounts)
    expect(update.original_amount).toBeNull()
    expect(update.original_currency).toBeNull()
  })

  it('requires an explicit investment direction and paired original charge', () => {
    const draft = {
      ...startingReviewDraft(expense, {}),
      kind: 'investment' as const,
      investmentDirection: '' as const,
      originalAmount: '100',
      originalCurrency: '',
    }
    expect(validateReviewDraft(expense, draft, accounts)).toMatch(/buy or sell/i)
    expect(
      validateReviewDraft(expense, { ...draft, investmentDirection: 'buy' }, accounts),
    ).toMatch(/amount and currency/i)
    expect(
      validateReviewDraft(
        expense,
        { ...draft, investmentDirection: 'buy', originalCurrency: 'ZZZ' },
        accounts,
      ),
    ).toMatch(/supported original currency/i)
  })
})

describe('review card title', () => {
  const buy = {
    ...expense,
    kind: 'investment',
    category: null,
    description: 'Share purchase',
    fromAccountId: 'usd',
  } as Txn

  it('names an investment row with no payee by its own description, not "No payee yet"', () => {
    expect(summaryTitle(startingReviewDraft(buy, {}), accounts, buy.description)).toBe(
      'Share purchase',
    )
  })

  it('still names an investment row by its payee when it has one', () => {
    const linked = { ...buy, payeeId: 'pay_1' } as Txn
    expect(summaryTitle(startingReviewDraft(linked, { pay_1: 'IBKR' }), accounts, 'x')).toBe('IBKR')
  })

  it('never lets a bank description pose as an expense payee', () => {
    expect(summaryTitle(startingReviewDraft(expense, {}), accounts, expense.description)).toBe('')
  })
})
