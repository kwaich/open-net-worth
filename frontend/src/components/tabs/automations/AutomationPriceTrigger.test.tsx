// @vitest-environment jsdom

import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Automation, AutomationTrigger } from '../../../api/automations'
import { AutomationEditorModal } from './AutomationEditorModal'
import { cadencePrompt } from './cadencePrompt'
import { automationTriggerLabel } from './AutomationTriggerSummary'

const price = (direction: string, value: number): AutomationTrigger => ({
  kind: 'event',
  event: 'price',
  when: { symbol: 'NVDA', direction, value },
  limit: 'day',
})

describe('price trigger directions', () => {
  it('labels each percent direction by its own sign', () => {
    expect(automationTriggerLabel(price('rise', 5))).toBe(
      'When NVDA rises more than 5% in a day • at most once a day',
    )
    expect(automationTriggerLabel(price('fall', 5))).toBe(
      'When NVDA falls more than 5% in a day • at most once a day',
    )
    expect(automationTriggerLabel(price('move', 5))).toBe(
      'When NVDA moves more than 5% in a day • at most once a day',
    )
  })

  it('marks the threshold as a percent only for percent directions', () => {
    const renderWith = (trigger: AutomationTrigger) =>
      render(
        <AutomationEditorModal
          open
          onClose={vi.fn()}
          onSubmit={vi.fn()}
          initialTrigger={trigger}
          options={{ accounts: [], instruments: [] }}
        />,
      )
    const { unmount } = renderWith(price('fall', 5))
    expect(screen.getByText('% vs. prior close')).toBeTruthy()
    unmount()

    renderWith(price('below', 90))
    expect(screen.queryByText('% vs. prior close')).toBeNull()
  })
})

describe('custom cadence prompt', () => {
  it('carries an unsaved draft’s name and instructions', () => {
    const prompt = cadencePrompt('Tax reminder', 'Remind me to pay tax.')
    expect(prompt).toContain('Name: Tax reminder')
    expect(prompt).toContain('What it should do: Remind me to pay tax.')
  })

  it('names a saved automation by id', () => {
    const prompt = cadencePrompt('ignored', '', {
      id: 'automation_1',
      name: 'Weekly report',
    } as Automation)
    expect(prompt).toContain('“Weekly report” (automation_1)')
  })
})
