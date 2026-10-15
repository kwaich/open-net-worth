// @vitest-environment jsdom
//
// Rendering coverage for MergeEnvelopeModal (CONF-2) and NewEnvelopeModal (CONF-10) — the two
// modals in this file whose prototype-conformance bugs are copy/default-derivation, not
// pure-function contracts, so they need a mounted render rather than a bare function call.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { MergeEnvelopeModal, NewEnvelopeModal } from './budgetEnvelopeOps'
import type { LocalCat, LocalGroup } from './useBudgetModel'
import type { CategoryMeta } from '../../../hooks/useBudget'

afterEach(() => cleanup())

function mkCat(
  overrides: Partial<LocalCat> & Pick<LocalCat, 'key' | 'label' | 'envelopeId' | 'categoryIds'>,
): LocalCat {
  return {
    color: '#34b08a',
    assigned: 0,
    activity: 0,
    available: 0,
    target: 0,
    tt: null,
    cad: null,
    status: 'none',
    needed: 0,
    ...overrides,
  }
}
function mkGroup(
  overrides: Partial<LocalGroup> & Pick<LocalGroup, 'id' | 'name' | 'cats'>,
): LocalGroup {
  return { collapsed: false, subtotal: { assigned: 0, activity: 0, available: 0 }, ...overrides }
}

describe('MergeEnvelopeModal — CONF-2 empty-envelope bullet copy', () => {
  it('a normal (non-empty) source reads "N categories from X move into Y"', () => {
    const source = mkCat({
      key: 'groceries',
      label: 'Groceries',
      envelopeId: 'env-groceries',
      categoryIds: ['groceries'],
    })
    const groups: LocalGroup[] = [
      mkGroup({
        id: 'g-a',
        name: 'Everyday',
        cats: [
          source,
          mkCat({
            key: 'dining',
            label: 'Dining',
            envelopeId: 'env-dining',
            categoryIds: ['dining'],
          }),
        ],
      }),
    ]
    render(
      <MergeEnvelopeModal
        source={source}
        allGroups={groups}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )
    expect(screen.getByText(/1 category from Groceries moves into/)).toBeTruthy()
  })

  it("an empty (zero-category) source reads the prototype's special-cased copy", () => {
    const empty = mkCat({
      key: 'env-empty',
      label: 'Emergency fund',
      envelopeId: 'env-empty',
      categoryIds: [],
    })
    const groups: LocalGroup[] = [
      mkGroup({
        id: 'g-a',
        name: 'Everyday',
        cats: [
          empty,
          mkCat({
            key: 'dining',
            label: 'Dining',
            envelopeId: 'env-dining',
            categoryIds: ['dining'],
          }),
        ],
      }),
    ]
    render(
      <MergeEnvelopeModal
        source={empty}
        allGroups={groups}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )
    expect(screen.getByText(/This empty envelope folds into/)).toBeTruthy()
    expect(screen.queryByText(/0 categor/)).toBeNull()
  })
})

describe('NewEnvelopeModal — CONF-10 empty-envelope default colour cycles by row count', () => {
  const categories: CategoryMeta[] = []
  const groups: LocalGroup[] = []

  it('with rowCount omitted, falls back to the first palette colour (unchanged default)', () => {
    const onConfirm = vi.fn()
    render(
      <NewEnvelopeModal
        groupName="Everyday"
        allGroups={groups}
        categories={categories}
        hiddenKeys={new Set()}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText('e.g. Everyday food'), {
      target: { value: 'Rainy day' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Create empty envelope/i }))
    expect(onConfirm).toHaveBeenCalledWith('Rainy day', '#34b08a', []) // NEWCAT_PALETTE[0]
  })

  it('confirming an empty envelope with rowCount=2 submits the 3rd palette colour (index 2 % length)', () => {
    const onConfirm = vi.fn()
    render(
      <NewEnvelopeModal
        groupName="Everyday"
        allGroups={groups}
        categories={categories}
        hiddenKeys={new Set()}
        rowCount={2}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText('e.g. Everyday food'), {
      target: { value: 'Rainy day' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Create empty envelope/i }))
    expect(onConfirm).toHaveBeenCalledWith('Rainy day', '#e0a032', []) // NEWCAT_PALETTE[2]
  })

  it('confirming a SECOND empty envelope (rowCount=3) lands on a DIFFERENT colour than rowCount=2', () => {
    const onConfirm = vi.fn()
    render(
      <NewEnvelopeModal
        groupName="Everyday"
        allGroups={groups}
        categories={categories}
        hiddenKeys={new Set()}
        rowCount={3}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />,
    )
    fireEvent.change(screen.getByPlaceholderText('e.g. Everyday food'), {
      target: { value: 'Another fund' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Create empty envelope/i }))
    expect(onConfirm).toHaveBeenCalledWith('Another fund', '#9a7fd0', []) // NEWCAT_PALETTE[3]
  })
})
