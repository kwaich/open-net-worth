// @vitest-environment jsdom
//
// `automation` is a registered Lucius mention type, so an automation could be @-mentioned —
// but the card emitted no data-entity-id, so it could not be dragged into the dock or
// right-clicked "Ask Lucius" (owner, 2026-08-11). DragRightClick binds globally on
// [data-entity-id][draggable]; the attributes ARE the wiring, so this locks them in at the
// leaf component, the same way tabs/accounts/lucius.entityWiring.test.tsx does.

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { AutomationCard } from './AutomationCard'
import type { Automation } from '../../../api/automations'

afterEach(cleanup)

function makeAutomation(over: Partial<Automation> = {}): Automation {
  return {
    id: 'task-0fae2fb5',
    name: 'Log trade & transactions from emails',
    description: 'Scan emails and log trades',
    instructions: 'Do the thing.',
    status: 'active',
    trigger: { kind: 'schedule', type: 'interval', seconds: 43200 },
    ...over,
  } as Automation
}

describe('AutomationCard Lucius wiring', () => {
  it('emits the entity id, label and draggable so drag + Ask Lucius work', () => {
    const { container } = render(
      <AutomationCard
        automation={makeAutomation()}
        onOpen={() => {}}
        onRunNow={() => {}}
        onToggle={() => {}}
        onEdit={() => {}}
        onDuplicate={() => {}}
        onDelete={() => {}}
      />,
    )
    const card = container.querySelector<HTMLElement>('[data-entity-id="automation:task-0fae2fb5"]')
    expect(card).toBeTruthy()
    expect(card!.getAttribute('data-entity-label')).toBe('Log trade & transactions from emails')
    expect(card!.getAttribute('draggable')).toBe('true')
  })
})
