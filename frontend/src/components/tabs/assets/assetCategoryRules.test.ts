// Per-category rules shared by the create wizard and the asset page's section editors.
import { describe, expect, it } from 'vitest'
import {
  attachmentsHint,
  CATEGORY_FACTS,
  COMMON_FACTS,
  canHaveLoan,
  canHaveStorageLocation,
  guidedFactsFor,
  namePlaceholder,
  notePlaceholder,
  purchaseCopy,
  sanitizeFacts,
  showsInsured,
  valueHelp,
  valueLabel,
} from './assetCategoryRules'

const BUILT_INS = ['property', 'vehicle', 'watch', 'art', 'metal', 'policy', 'other']

describe('canHaveLoan', () => {
  it('asks about a loan only for property, vehicles and custom categories', () => {
    expect(BUILT_INS.filter(canHaveLoan)).toEqual(['property', 'vehicle'])
    expect(canHaveLoan('c_boats')).toBe(true)
  })
})

describe('showsInsured', () => {
  it('never asks whether a policy is insured', () => {
    expect(BUILT_INS.filter((c) => !showsInsured(c))).toEqual(['policy'])
    expect(showsInsured('c_boats')).toBe(true)
  })
})

describe('the value field', () => {
  it('asks a policy for its cash value, everything else for what it is worth', () => {
    expect(valueLabel('policy')).toBe('Cash value today')
    expect(valueHelp('policy')).toBe(
      'The surrender value on your latest statement — not the sum assured.',
    )
    for (const c of ['property', 'watch', 'c_boats']) {
      expect(valueLabel(c)).toBe('Worth today')
      expect(valueHelp(c)).toBe('Your best estimate is fine')
    }
  })
})

describe('attachmentsHint', () => {
  it('names the paperwork that goes with each category — documents, never proof of a price', () => {
    expect(BUILT_INS.map(attachmentsHint)).toEqual([
      'Keep its documents here — the purchase agreement, title deed, receipts and photos.',
      'Keep its documents here — registration, insurance, service records and photos.',
      'Keep its documents here — box and papers, certificates, the receipt and photos.',
      'Keep its documents here — certificate of authenticity, invoice, provenance and photos.',
      'Keep its documents here — purchase invoices, assay certificates and storage statements.',
      'Keep its documents here — the policy contract and each annual statement.',
      'Keep its documents here — contracts, receipts, warranties and photos.',
    ])
    expect(attachmentsHint('c_boats')).toBe(
      'Keep its documents here — contracts, receipts, warranties and photos.',
    )
    for (const c of BUILT_INS) expect(attachmentsHint(c)).not.toMatch(/value|price shows|based on/i)
  })
})

describe('notePlaceholder', () => {
  it("suggests where each category's figure usually comes from", () => {
    expect(BUILT_INS.map(notePlaceholder)).toEqual([
      'e.g. Bank valuation',
      'e.g. Dealer quote',
      'e.g. Chrono24 median',
      'e.g. Auction estimate',
      'e.g. Spot price',
      'e.g. Annual statement',
      'e.g. Where the figure came from',
    ])
    expect(notePlaceholder('c_boats')).toBe('e.g. Where the figure came from')
  })
})

describe('purchaseCopy', () => {
  it('words the purchase step and the Purchase section for a bought thing', () => {
    expect(purchaseCopy('property')).toEqual({
      question: 'How much did you pay?',
      intro: 'Optional — adds your gain and yearly return.',
      costLabel: 'Bought for',
      dateLabel: 'Bought on',
      cardTitle: 'Purchase',
      chipLabel: 'What you paid',
      editIntro: 'Adds your gain and yearly return.',
      chartLabel: 'Bought for',
    })
    expect(purchaseCopy('c_boats')).toEqual(purchaseCopy('watch'))
  })

  it('words them as premiums for a policy — no purchase, no purchase point', () => {
    expect(purchaseCopy('policy')).toEqual({
      question: 'How much have you paid in?',
      intro: "Optional. Shows whether the cash value is above what you've paid.",
      costLabel: 'Premiums paid so far',
      dateLabel: 'Policy started on',
      cardTitle: 'Premiums paid',
      chipLabel: 'What you’ve paid in',
      editIntro: "Shows whether the cash value is above what you've paid.",
      chartLabel: 'Premiums paid',
    })
  })

  it('never says "mark" — the word is internal', () => {
    for (const c of [...BUILT_INS, 'c_boats']) {
      expect(Object.values(purchaseCopy(c)).join(' ')).not.toMatch(/\bmark/i)
    }
  })
})

describe('namePlaceholder', () => {
  it('gives each category its own example and anything else a generic one', () => {
    expect(namePlaceholder('property')).toBe('e.g. 88 Marina Rise')
    expect(namePlaceholder('policy')).toBe('e.g. Great Eastern Endowment Plan')
    expect(namePlaceholder('c_boats')).toBe('Enter a recognizable name')
  })
})

describe('guided details', () => {
  it("returns a category's own prompts, else just where it's kept", () => {
    expect(guidedFactsFor('property')).toBe(CATEGORY_FACTS.property)
    expect(guidedFactsFor('vehicle').map(([label]) => label)).toEqual([
      'Plate number',
      'COE expires',
      'Mileage',
    ])
    expect(guidedFactsFor('c_boats')).toBe(COMMON_FACTS)
    expect(guidedFactsFor(null)).toBe(COMMON_FACTS)
  })

  it('asks where portable things are kept — never a building, a car or a policy', () => {
    expect(BUILT_INS.filter(canHaveStorageLocation)).toEqual(['watch', 'art', 'metal', 'other'])
    expect(canHaveStorageLocation('c_boats')).toBe(true)
  })

  it('drops blank rows and trims the rest before they reach the wire', () => {
    expect(
      sanitizeFacts([
        [' Tenure ', ' Freehold '],
        ['', ''],
        ['Floor area', '  '],
      ]),
    ).toEqual([['Tenure', 'Freehold']])
  })
})
