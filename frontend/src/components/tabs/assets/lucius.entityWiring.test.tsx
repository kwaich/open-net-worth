// @vitest-environment jsdom
//
// `asset` is a registered Lucius mention type (backend descriptor
// services/mentions/descriptors/new_types.py, order=35) — this locks in that the Assets tab's
// per-asset rows actually stamp `data-entity-id`, so an asset can be dragged into the dock,
// right-clicked "Ask Lucius about this", or harvested as an "on this screen" row. Same shape as
// tabs/commitments/lucius.entityWiring.test.tsx — the two "peer" class cards (Cash,
// Investments) are aggregate category cards, not individual asset entities, so ClassCard is
// deliberately NOT covered here.

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import type { AssetCategory, AssetLoan, AssetMark, OwnedAsset } from '../../../types/assets'
import { AssetCard, Rail } from './register'
import { classCardsFrom } from './assetsData'
import type { AssetsSummary } from '../../../types/assets'

afterEach(cleanup)

function mark(overrides: Partial<AssetMark> = {}): AssetMark {
  return {
    id: 'mk1',
    kind: 'valuation',
    date: '2026-01-01',
    value: 1000,
    note: '',
    by: 'you',
    created_at: '2026-01-01T00:00:00Z',
    edited_at: null,
    ...overrides,
  }
}

function asset(overrides: Partial<OwnedAsset> = {}): OwnedAsset {
  return {
    id: 'oa_7f3a1b2c9d4e',
    name: 'Volvo XC60',
    category: 'vehicle',
    description: '',
    photo_ref: null,
    facts: [],
    notes: 'Garaged, never tracked',
    cost: null,
    cost_date: null,
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 52000,
    valued_on: '2026-01-01',
    share_value: 52000,
    marks: [mark()],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

const CATS: AssetCategory[] = [
  {
    key: 'vehicle',
    label: 'Vehicle',
    plural: 'Vehicles',
    color: 'var(--cat-vehicle)',
    custom: false,
  },
]

const LOANS: AssetLoan[] = []

describe('AssetCard Lucius wiring', () => {
  it('emits the entity id, label and draggable so drag + Ask Lucius work', () => {
    const a = asset()
    const { container } = render(<AssetCard a={a} cats={CATS} loans={LOANS} onOpen={() => {}} />)

    const row = container.querySelector<HTMLElement>('[data-entity-id="asset:oa_7f3a1b2c9d4e"]')
    expect(row).toBeTruthy()
    expect(row!.getAttribute('data-entity-label')).toBe('Volvo XC60')
    expect(row!.getAttribute('draggable')).toBe('true')
  })

  it('emits the descriptor-allowlisted context fields for the screen snapshot', () => {
    const a = asset()
    const { container } = render(<AssetCard a={a} cats={CATS} loans={LOANS} onOpen={() => {}} />)

    const row = container.querySelector<HTMLElement>('[data-entity-id="asset:oa_7f3a1b2c9d4e"]')!
    expect(row.getAttribute('data-ctx-type')).toBe('asset')

    // filterByDescriptor keeps only the backend descriptor's allowlist
    // (name/category/value/valued_on/share_pct/notes) — description never reaches the DOM.
    const fields = row.getAttribute('data-ctx-fields') ?? ''
    expect(fields).toContain('vehicle')
    expect(fields).toContain('Garaged, never tracked')
  })

  it('falls back to "(unnamed)" for an asset with no name', () => {
    const a = asset({ name: '' })
    const { container } = render(<AssetCard a={a} cats={CATS} loans={LOANS} onOpen={() => {}} />)
    const row = container.querySelector<HTMLElement>('[data-entity-id="asset:oa_7f3a1b2c9d4e"]')!
    expect(row.getAttribute('data-entity-label')).toBe('(unnamed)')
  })
})

describe('Rail "needs a look" queue Lucius wiring', () => {
  function summary(): AssetsSummary {
    return {
      own: 1,
      owe: 0,
      net_worth: 1,
      unrealized: 0,
      classes: {
        cash: {
          value: 0,
          sub: '',
          yield_label: null,
          gain: null,
          gain_pct: null,
          as_of: 'today',
          count_label: '',
          rows: null,
        },
        investments: {
          value: 0,
          sub: '',
          gain: 0,
          gain_pct: 0,
          as_of: 'today',
          count_label: '',
          rows: null,
        },
        retirement: null,
        receivable: null,
        debt: null,
      },
      debt: null,
      composition: { total: 0, rows: [] },
    }
  }

  it('does not add a review-queue entity row', () => {
    const s = summary()
    const { container } = render(
      <Rail cats={[]} classes={classCardsFrom(s)} summary={s} loans={LOANS} />,
    )
    expect(container.querySelector('.as-todo-i')).toBeNull()
  })
})
