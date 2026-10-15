// @vitest-environment jsdom
//
// `commitment` is a registered Lucius mention type (backend descriptor
// services/mentions/descriptors/new_types.py, order=16) — but the list row emitted no
// data-entity-id, so a commitment could not be dragged into the dock, right-clicked
// "Ask Lucius about this", or harvested as an "on this screen" row (owner, 2026-08-23).
//
// dragRightClick binds globally on [data-entity-id][draggable]; the attributes ARE the
// wiring, so this locks them in at the leaf component — the same shape as
// tabs/automations/lucius.entityWiring.test.tsx and tabs/accounts/lucius.entityWiring.test.tsx.

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { CommitmentRows } from './rows'
import type { Commitment } from '../../../types/commitments'

afterEach(cleanup)

function makeCommitment(over: Partial<Commitment> = {}): Commitment {
  return {
    id: 'cm_7f3a1b2c',
    name: 'Apartment down payment',
    kind: 'purchase',
    currency: 'SGD',
    status: 'active',
    linked_position_id: null,
    linked_account_id: null,
    note: 'Second tranche due after handover',
    created_at: '2026-01-15',
    installments: [],
    funding_plan: [],
    ...over,
  } as Commitment
}

function renderRows(list: Commitment[]) {
  return render(
    <CommitmentRows
      list={list}
      fx={{ SGD: 1 }}
      onOpen={() => {}}
      filter="all"
      setFilter={() => {}}
      sort="next"
      setSort={() => {}}
    />,
  )
}

describe('CommitmentRows Lucius wiring', () => {
  it('emits the entity id, label and draggable so drag + Ask Lucius work', () => {
    const { container } = renderRows([makeCommitment()])

    const row = container.querySelector<HTMLElement>('[data-entity-id="commitment:cm_7f3a1b2c"]')
    expect(row).toBeTruthy()
    expect(row!.getAttribute('data-entity-label')).toBe('Apartment down payment')
    expect(row!.getAttribute('draggable')).toBe('true')
  })

  it('emits the descriptor-allowlisted context fields for the screen snapshot', () => {
    const { container } = renderRows([makeCommitment()])

    const row = container.querySelector<HTMLElement>('[data-entity-id="commitment:cm_7f3a1b2c"]')!
    expect(row.getAttribute('data-ctx-type')).toBe('commitment')

    // filterByDescriptor keeps only the backend descriptor's allowlist
    // (name/kind/currency/status/note/created_at) — nested installments never reach the DOM.
    const fields = row.getAttribute('data-ctx-fields') ?? ''
    expect(fields).toContain('purchase')
    expect(fields).toContain('active')
    expect(fields).not.toContain('installments')
  })

  it('wires every row in the list, not just the first', () => {
    const { container } = renderRows([
      makeCommitment(),
      makeCommitment({ id: 'cm_99887766', name: 'Car loan' }),
    ])

    expect(container.querySelectorAll('[data-entity-id^="commitment:"]').length).toBe(2)
    expect(
      container
        .querySelector('[data-entity-id="commitment:cm_99887766"]')!
        .getAttribute('data-entity-label'),
    ).toBe('Car loan')
  })
})
