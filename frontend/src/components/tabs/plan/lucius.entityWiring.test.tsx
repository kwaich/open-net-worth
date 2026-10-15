// @vitest-environment jsdom
//
// Regression: Plan-tab Lucius mention wiring (ADR 2026-07-11-envelope-first-class-
// mention-type). The rule — THE SURFACE DECIDES THE MENTION TYPE:
//   - a Budget-table row IS the envelope → data-entity-id="envelope:<envelope id>",
//     draggable even when LOCKED (bot-managed commitment/card-payment rows: reorder
//     stays disabled separately, the mention drag must still work);
//   - a Manage-categories row IS the category → data-entity-id="category:<category id>",
//     drag paused while renaming (InlineEdit keeps text selection);
//   - both carry data-entity-color (the entity's own display color) so chips tint.
// Mirrors accounts/lucius.entityWiring.test.tsx, added after the Accounts tab silently
// lost its wiring once.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { CategoryRow } from './budget'
import { CatMgrRow } from './CatMgrRow'
import type { LocalCat } from './useBudgetModel'

afterEach(cleanup)

function makeCat(over: Partial<LocalCat> = {}): LocalCat {
  return {
    key: 'env_groceries',
    label: 'Groceries',
    color: '#22aa66',
    assigned: 500,
    activity: -120,
    available: 380,
    target: 0,
    tt: null,
    cad: null,
    status: 'none',
    needed: 0,
    envelopeId: 'env_groceries',
    categoryIds: ['cat_food'],
    ...over,
  }
}

const dndStub = {
  catStart: () => () => {},
  groupStart: () => () => {},
  rowOver: () => () => {},
  rowDrop: () => () => {},
  groupOver: () => () => {},
  groupDrop: () => () => {},
  end: () => {},
}

function renderCategoryRow(c: LocalCat, locked = false) {
  const noop = () => {}
  return render(
    <CategoryRow
      c={c}
      gid="g1"
      dnd={dndStub}
      over={null}
      otherGroups={[]}
      locked={locked}
      month="2026-07"
      allGroups={[]}
      catLabel={(k) => k}
      catColor={() => '#888888'}
      rta={0}
      onOpenCommitment={noop}
      onOpenCardPayment={noop}
      onAssign={noop}
      onAction={noop}
      onRename={noop}
      onRecolor={noop}
      onMoveTo={noop}
      onToggleHide={noop}
      onDeleteEnvelope={noop}
      onEditTarget={noop}
      onTransfer={noop}
      onMerge={noop}
      onSplit={noop}
      onAddCats={noop}
    />,
  )
}

describe('Budget table row — envelope entity wiring', () => {
  it('emits envelope:<envelope id> with label + color, draggable', () => {
    const { container } = renderCategoryRow(makeCat())
    const row = container.querySelector('[data-entity-id="envelope:env_groceries"]')
    expect(row).not.toBeNull()
    expect(row!.getAttribute('data-entity-label')).toBe('Groceries')
    expect(row!.getAttribute('data-entity-color')).toBe('#22aa66')
    expect(row!.getAttribute('draggable')).toBe('true')
  })

  it('a LINKED row still emits the ENVELOPE ref, never its category (the surface rule)', () => {
    const { container } = renderCategoryRow(
      makeCat({
        envelopeId: 'env_eth',
        key: 'env_eth',
        label: 'ETH DCA',
        categoryIds: ['cat_eth'],
      }),
    )
    expect(container.querySelector('[data-entity-id="envelope:env_eth"]')).not.toBeNull()
    expect(container.querySelector('[data-entity-id^="category:"]')).toBeNull()
  })

  it('a LOCKED (bot-managed) row stays draggable for the mention drag', () => {
    const { container } = renderCategoryRow(
      makeCat({ envelopeId: 'env_cc', key: 'env_cc', label: 'Amex', categoryIds: [] }),
      true,
    )
    const row = container.querySelector('[data-entity-id="envelope:env_cc"]')
    expect(row).not.toBeNull()
    expect(row!.getAttribute('draggable')).toBe('true')
  })
})

describe('Manage categories row — category entity wiring', () => {
  const cat = { id: 'cat_bonus', label: 'Bonus', kind: 'income', color: '#cc8844' }
  const rowProps = {
    envelope: undefined,
    labelOf: (k: string) => k,
    onStartRename: () => {},
    onCancelRename: () => {},
    onRename: () => {},
    onRecolor: () => {},
    onToggleHide: () => {},
    onDelete: () => {},
  }

  it('emits category:<category id> with label + color, draggable', () => {
    const { container } = render(<CatMgrRow c={cat} renaming={false} {...rowProps} />)
    const row = container.querySelector('[data-entity-id="category:cat_bonus"]')
    expect(row).not.toBeNull()
    expect(row!.getAttribute('data-entity-label')).toBe('Bonus')
    expect(row!.getAttribute('data-entity-color')).toBe('#cc8844')
    expect(row!.getAttribute('draggable')).toBe('true')
  })

  it('drag pauses while renaming', () => {
    const { container } = render(<CatMgrRow c={cat} renaming={true} {...rowProps} />)
    const row = container.querySelector('[data-entity-id="category:cat_bonus"]')
    expect(row).not.toBeNull()
    expect(row!.getAttribute('draggable')).toBe('false')
  })
})
