// @vitest-environment jsdom
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'

const navigateMock = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigateMock }))

import { _resetBusForTests, onScreenCommand } from '../../lucius/bus'
import { ScreenCommandChip } from './ScreenCommandChip'

beforeEach(() => {
  _resetBusForTests()
  navigateMock.mockClear()
})

it('renders the mapped label and, on click, re-dispatches the command and navigates', () => {
  const seen = vi.fn()
  onScreenCommand('plan.', seen)
  render(
    <ScreenCommandChip
      command={{ name: 'plan.stage_whatif', args: { retireAge: 45 } }}
      route="plan-outlook"
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: /view on outlook/i }))
  expect(seen).toHaveBeenCalledWith({ name: 'plan.stage_whatif', args: { retireAge: 45 } })
  expect(navigateMock).toHaveBeenCalledOnce()
  // Must be the canonical /plan slug — the legacy /transactions alias redirects
  // WITHOUT forwarding search, which silently drops the ?sub=outlook deep-link.
  const navArg = navigateMock.mock.calls[0]?.[0] as { to?: string; search?: unknown }
  expect(navArg?.to).toBe('/plan')
  const searchFn = navArg?.search as (prev: Record<string, unknown>) => Record<string, unknown>
  expect(searchFn({})).toEqual({ sub: 'outlook' })
})

it('renders nothing for an unknown route key', () => {
  const { container } = render(
    <ScreenCommandChip command={{ name: 'x.y', args: {} }} route="nope" />,
  )
  expect(container.firstChild).toBeNull()
})

it('routes a parked Automation draft to the shared review editor screen', () => {
  render(
    <ScreenCommandChip
      command={{ name: 'automations.review_draft', args: { automation: {} } }}
      route="automations"
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: /review automation/i }))
  expect(navigateMock.mock.calls.at(-1)?.[0]).toEqual(
    expect.objectContaining({ to: '/automations' }),
  )
})
