// @vitest-environment jsdom
//
// The asset page's record — Ownership and Purchase beside Details and Notes, each drawn only
// when it has something, then one row of "+" chips for what is missing. Pure rendering (no
// requests); synthetic data only.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, within } from '@testing-library/react'
import type { AssetCategory, AssetLoan, OwnedAsset } from '../../../types/assets'
import { setBaseCurrency } from '../../../utils/format'
import { fmt } from './assetsData'
import { AssetSections } from './sections'

setBaseCurrency('SGD')
afterEach(cleanup)

const CATS: AssetCategory[] = [
  {
    key: 'property',
    label: 'Property',
    plural: 'Property',
    color: 'var(--cat-property)',
    custom: false,
  },
  {
    key: 'vehicle',
    label: 'Vehicle',
    plural: 'Vehicles',
    color: 'var(--cat-vehicle)',
    custom: false,
  },
  {
    key: 'watch',
    label: 'Watch & jewellery',
    plural: 'Watches & jewellery',
    color: 'var(--cat-watch)',
    custom: false,
  },
  {
    key: 'policy',
    label: 'Insurance & endowments',
    plural: 'Insurance & endowments',
    color: 'var(--cat-policy)',
    custom: false,
  },
]

const LOAN: AssetLoan = {
  id: 'acc_loan0001',
  name: 'Test Mortgage',
  balance: 310000,
  rate_label: '3.1%',
  term_label: 'to 2044',
  held_by: { id: 'oa_test000001', name: 'Test Condo' },
}

function asset(overrides: Partial<OwnedAsset> = {}): OwnedAsset {
  return {
    id: 'oa_test000001',
    name: 'Test Condo',
    category: 'property',
    description: '',
    photo_ref: null,
    facts: [],
    notes: '',
    cost: null,
    cost_date: null,
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 850000,
    valued_on: '2026-09-26',
    share_value: 850000,
    marks: [],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2026-09-26T10:51:19.358288+00:00',
    updated_at: '2026-09-26T10:51:19.358288+00:00',
    ...overrides,
  }
}

function renderRail(overrides: Partial<OwnedAsset> = {}, loans: AssetLoan[] = []) {
  const props = { a: asset(overrides), cats: CATS, loans, onNav: vi.fn(), onEdit: vi.fn() }
  const utils = render(<AssetSections {...props} />)
  return { ...props, ...utils }
}

/** The group titled `title`. */
function group(title: string): HTMLElement {
  const found = document.querySelector(`section.as-rec-group[aria-label="${title}"]`)
  if (!found) throw new Error(`no ${title} group`)
  return found as HTMLElement
}

const titles = () =>
  Array.from(document.querySelectorAll('.as-rec-title')).map((el) => el.textContent)

/** Each row of a group as [label, value]. */
const rows = (el: HTMLElement) =>
  Array.from(el.querySelectorAll('.as-rec-row')).map((r) => [
    r.querySelector('dt')?.textContent,
    r.querySelector('dd')?.textContent,
  ])

/** The "+" chips, as their visible labels. */
const chips = () =>
  Array.from(document.querySelectorAll('.as-rec-add button')).map((b) =>
    b.textContent?.replace(/^\+/, ''),
  )

describe('AssetSections — the record', () => {
  const FULL: Partial<OwnedAsset> = {
    loan_account_id: LOAN.id,
    cost: 620000,
    cost_date: '2019-06-15',
    facts: [['Address or unit', '#14-02, 88 Marina Rise']],
    notes: 'The receipt is in the safe deposit box.',
  }

  it('draws Ownership and Purchase beside Details and Notes — no boxes, no "Added" caption', () => {
    const { container } = renderRail(FULL, [LOAN])
    expect(titles()).toEqual(['Ownership', 'Purchase', 'Details', 'Notes'])
    const cols = Array.from(container.querySelectorAll('.as-rec-col')).map((c) =>
      Array.from(c.querySelectorAll('.as-rec-title')).map((t) => t.textContent),
    )
    expect(cols).toEqual([
      ['Ownership', 'Purchase'],
      ['Details', 'Notes'],
    ])
    expect(container.querySelector('.as-rcard')).toBeNull()
    expect(container.querySelector('aside')).toBeNull()
    expect(container.textContent).not.toMatch(/Added \w{3} \d{4}/)
  })

  it("opens each group's own editor from its Edit", () => {
    const { onEdit } = renderRail(FULL, [LOAN])
    for (const name of ['Edit ownership', 'Edit purchase', 'Edit details', 'Edit notes'])
      fireEvent.click(within(document.body).getByRole('button', { name }))
    expect(onEdit.mock.calls.map(([s]) => s)).toEqual(['ownership', 'purchase', 'details', 'notes'])
  })

  it('a new asset is one group and a row of chips — never an empty box or a grey sentence', () => {
    const { onEdit, container } = renderRail({ category: 'vehicle' })
    expect(titles()).toEqual(['Ownership'])
    expect(container.querySelectorAll('.as-rec-col')).toHaveLength(1)
    expect(chips()).toEqual([
      'Link a loan',
      'What you paid',
      'Plate number',
      'COE expires',
      'Mileage',
      'Custom detail',
      'A note',
    ])
    for (const name of [
      'Link a loan',
      'Add what you paid',
      'Add plate number',
      'Add a custom detail',
      'Add a note',
    ])
      fireEvent.click(within(container).getByRole('button', { name }))
    expect(onEdit.mock.calls.map(([s]) => s)).toEqual([
      'ownership',
      'purchase',
      'details',
      'custom-detail',
      'notes',
    ])
  })

  it('a record with everything in it still offers a detail of your own — never hidden', () => {
    // Owner 2026-09-28: "for watch, user can't add additional fields?" — the only way in was
    // "Add a detail" at the foot of the Details editor.
    const { container, onEdit } = renderRail({
      category: 'watch',
      cost: 9000,
      cost_date: '2020-01-01',
      notes: 'In the safe.',
      facts: [
        ["Where it's kept", 'Home safe'],
        ['Brand & reference', 'Rolex 124060'],
        ['Serial number', 'X1'],
        ['Box & papers', 'Yes'],
      ],
    })
    expect(chips()).toEqual(['Custom detail'])
    fireEvent.click(within(container).getByRole('button', { name: 'Add a custom detail' }))
    expect(onEdit).toHaveBeenLastCalledWith('custom-detail')
  })
})

describe('AssetSections — Ownership', () => {
  it('shows your share and who with, and the loan with what is yours and what is owed', () => {
    const { onNav } = renderRail(
      { share_pct: 50, share_note: 'Joint with A. Lim', loan_account_id: LOAN.id },
      [LOAN],
    )
    const own = group('Ownership')
    expect(rows(own)).toEqual([
      ['Your share', '50% — Joint with A. Lim'],
      ['Secured by', 'Test Mortgage'],
      ['Equity', `${fmt(540000)} · 64%`],
      ['Owed', `${fmt(310000)} · 36%`],
    ])
    expect(own.textContent).toContain('3.1% · to 2044')
    expect(own.querySelector('.as-split')).toBeTruthy()
    fireEvent.click(within(own).getByRole('button', { name: 'See this loan in Accounts' }))
    expect(onNav).toHaveBeenCalledWith('accounts')
    expect(chips()).not.toContain('Link a loan')
  })

  it('says a loan that also secures another asset does', () => {
    renderRail({ loan_account_id: LOAN.id, id: 'oa_other00001' }, [LOAN])
    expect(group('Ownership').textContent).toContain('This loan also secures Test Condo.')
  })

  it('offers to link a loan to a property, never to a watch', () => {
    renderRail({ category: 'property' })
    expect(rows(group('Ownership'))).toEqual([['Your share', '100%']])
    expect(chips()).toContain('Link a loan')
    cleanup()
    renderRail({ category: 'watch' })
    expect(chips()).not.toContain('Link a loan')
  })
})

describe('AssetSections — Purchase', () => {
  it('shows what was paid and when', () => {
    renderRail({ cost: 620000, cost_date: '2019-06-15' })
    expect(rows(group('Purchase'))).toEqual([
      ['Bought for', fmt(620000)],
      ['Bought on', 'Jun 2019'],
    ])
    expect(chips()).not.toContain('What you paid')
  })

  it('is not drawn until something is recorded — a chip asks for it', () => {
    renderRail()
    expect(titles()).not.toContain('Purchase')
    expect(chips()).toContain('What you paid')
  })

  it('is "Premiums paid" for a policy', () => {
    renderRail({ category: 'policy', cost: 18000, cost_date: '2019-06-01' })
    expect(rows(group('Premiums paid')).map(([k]) => k)).toEqual([
      'Premiums paid so far',
      'Policy started on',
    ])
    cleanup()
    renderRail({ category: 'policy' })
    expect(chips()).toContain('What you’ve paid in')
  })
})

describe('AssetSections — Details', () => {
  it('nothing filled: no Details group, one chip per thing the category asks', () => {
    renderRail({ category: 'vehicle' })
    expect(titles()).not.toContain('Details')
    expect(chips()).toEqual(expect.arrayContaining(['Plate number', 'COE expires', 'Mileage']))
  })

  it('partly filled: its rows, and chips for the rest', () => {
    renderRail({ category: 'vehicle', facts: [['Plate number', 'SLA 4417 X']] })
    expect(rows(group('Details'))).toEqual([['Plate number', 'SLA 4417 X']])
    expect(chips()).toEqual(expect.arrayContaining(['COE expires', 'Mileage']))
    expect(chips()).not.toContain('Plate number')
  })

  it('one list: the category’s own, Insured when it is, then the other details — no subheading', () => {
    renderRail({
      category: 'vehicle',
      insured: true,
      facts: [
        ['Tenure', 'Freehold'],
        ['Plate number', 'SLA 4417 X'],
        ['COE expires', 'Feb 2032'],
        ['Mileage', '41,200 km'],
      ],
    })
    expect(rows(group('Details'))).toEqual([
      ['Plate number', 'SLA 4417 X'],
      ['COE expires', 'Feb 2032'],
      ['Mileage', '41,200 km'],
      ['Insured', 'Yes'],
      ['Tenure', 'Freehold'],
    ])
    expect(group('Details').textContent).not.toContain('Other details')
  })

  it('an unticked Insured is not shown — it is also what every asset starts with', () => {
    renderRail({ category: 'vehicle', facts: [['Mileage', '41,200 km']] })
    expect(group('Details').textContent).not.toContain('Insured')
  })

  it('a policy has no Insured row, and shows when it matures — or asks for it', () => {
    renderRail({ category: 'policy', maturity_date: '2041-06-01' })
    expect(rows(group('Details'))).toEqual([['Matures on', 'Jun 2041']])
    cleanup()
    renderRail({ category: 'policy' })
    expect(chips()).toContain('Maturity date')
  })
})

describe('AssetSections — Notes', () => {
  it('shows the note as written, or a chip for one', () => {
    renderRail({ notes: 'The receipt is in the safe deposit box.\nCall Jane.' })
    expect(group('Notes').querySelector('.as-rec-notes')?.textContent).toBe(
      'The receipt is in the safe deposit box.\nCall Jane.',
    )
    expect(chips()).not.toContain('A note')
    cleanup()
    renderRail({ notes: '' })
    expect(titles()).not.toContain('Notes')
    expect(chips()).toContain('A note')
  })
})
