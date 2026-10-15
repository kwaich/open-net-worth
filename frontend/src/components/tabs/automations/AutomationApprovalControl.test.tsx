// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import type { AutomationTrigger } from '../../../api/automations'
import { AutomationEditorModal } from './AutomationEditorModal'

const weekday: AutomationTrigger = {
  kind: 'schedule',
  type: 'weekdays',
  time: '08:00',
  tzid: 'Asia/Singapore',
}

afterEach(cleanup)

describe('Automation approval control', () => {
  it('shows the three approval modes with their icons and saves the selected policy', () => {
    const onSubmit = vi.fn()
    render(
      <AutomationEditorModal
        open
        onClose={vi.fn()}
        onSubmit={onSubmit}
        seed={{
          name: 'Morning brief',
          instructions: 'Review my money every weekday.',
          trigger: weekday,
          permission_mode: 'manual',
        }}
      />,
    )

    const trigger = screen.getByRole('button', {
      name: 'Permission mode: Manually approve',
    })
    expect(trigger.textContent).toContain('Manual')
    fireEvent.click(trigger)

    const menu = screen.getByRole('menu', { name: 'Permission mode' })
    const manual = within(menu).getByRole('menuitemradio', { name: 'Manually approve' })
    const automatic = within(menu).getByRole('menuitemradio', {
      name: 'Automatically approve',
    })
    const skip = within(menu).getByRole('menuitemradio', { name: 'Skip all approvals' })
    expect(manual.querySelector('.lucide-hand')).toBeTruthy()
    expect(automatic.querySelector('.lucide-fast-forward')).toBeTruthy()
    expect(skip.querySelector('.lucide-triangle-alert')).toBeTruthy()
    expect(manual.getAttribute('aria-checked')).toBe('true')
    expect(manual.querySelector('.lucide-check')).toBeTruthy()
    expect(automatic.querySelector('.lucide-check')).toBeNull()

    fireEvent.click(automatic)
    expect(
      screen.getByRole('button', { name: 'Permission mode: Automatically approve' }).textContent,
    ).toContain('Auto')
    expect(screen.queryByRole('menu', { name: 'Permission mode' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Create automation' }))
    expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ permission_mode: 'auto' }))
  })

  it('warns that Skip never pauses Lucius for unsafe actions', () => {
    render(
      <AutomationEditorModal
        open
        onClose={vi.fn()}
        onSubmit={vi.fn()}
        seed={{
          name: 'Unattended review',
          instructions: 'Review my money every weekday.',
          trigger: weekday,
          permission_mode: 'skip',
        }}
      />,
    )

    const trigger = screen.getByRole('button', {
      name: 'Permission mode: Skip all approvals',
    })
    // The RELATIONSHIP, not a literal id. PermissionModeControl became a shared ui/ primitive
    // on 2026-08-27 (the Lucius composer renders it too), so its ids come from `useId` — two
    // instances on one screen would otherwise both claim `automation-approval-*` and every
    // trigger's aria-describedby would point at the first one's tooltip.
    const describedBy = trigger.getAttribute('aria-describedby')
    expect(describedBy).toBeTruthy()
    const tooltip = screen.getByRole('tooltip')
    expect(tooltip.id).toBe(describedBy)
    expect(tooltip.textContent).toBe('Lucius never pauses, even for unsafe actions.')
  })
})
