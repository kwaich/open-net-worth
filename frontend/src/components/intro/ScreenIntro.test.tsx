// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { resetGuidedForTests } from './tourTarget'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

const state = vi.hoisted(() => ({
  accounts: [] as unknown[] | undefined,
  positions: [] as unknown[],
  commitments: [] as unknown[],
  unseen: true,
  replying: false,
  cashflowFill: null as 'empty' | 'filled' | null,
  planSub: 'budget' as string | undefined,
  profile: {} as Record<string, unknown>,
  markSeen: vi.fn(),
  budget: {
    home_engine_id: 'proportional',
    engines: [{ id: 'proportional', configured: false }],
    intro_dismissed: false,
  } as unknown,
}))

vi.mock('../../hooks/serverState', () => ({
  useAccounts: () => ({ data: state.accounts }),
  useProfile: () => ({ data: state.profile }),
}))
vi.mock('../../state/portfolio', () => ({
  usePortfolioContext: () => ({ positionRows: state.positions, hasLoaded: true }),
}))
vi.mock('../../hooks/useCommitments', () => ({
  useCommitments: () => ({ commitments: state.commitments, loading: false }),
}))
vi.mock('../tabs/plan/budgetLens/useBudgetEngines', () => ({
  useBudgetEngines: () => ({ data: state.budget }),
}))
vi.mock('@tanstack/react-router', () => ({
  useRouterState: ({ select }: { select: (s: unknown) => unknown }) =>
    select({ location: { search: { sub: state.planSub } } }),
}))
vi.mock('../../state/luciusChat', () => ({
  useLuciusChatOptional: () => ({ streaming: state.replying }),
}))
vi.mock('./screenState', () => ({
  useScreenFill: () => state.cashflowFill,
}))
vi.mock('./useSeenIntro', () => ({
  useSeenIntro: () => ({ unseen: state.unseen, markSeen: state.markSeen }),
}))

import { ScreenIntro } from './ScreenIntro'

beforeEach(() => {
  state.accounts = []
  state.positions = []
  state.commitments = []
  state.unseen = true
  state.replying = false
  state.cashflowFill = null
  state.planSub = 'budget'
  state.profile = {}
  state.markSeen = vi.fn()
  state.budget = {
    home_engine_id: 'proportional',
    engines: [{ id: 'proportional', configured: false }],
    intro_dismissed: false,
  }
})
afterEach(() => {
  cleanup()
  resetGuidedForTests() // every test is a fresh visit
})

const button = (name: string) => screen.getByRole('button', { name })
// Plan's intros wait for its sub-tab to hold still before they mount.
const settle = () => act(() => new Promise((r) => setTimeout(r, 400)))

it('introduces only the Plan tab that is open, one step each', async () => {
  render(<ScreenIntro screen="plan" onAskLucius={vi.fn()} />)
  await settle()
  expect(screen.getByRole('dialog', { name: 'Budget your way' })).toBeTruthy()
  expect(screen.queryByText(/Step 1 of/)).toBeNull()
  expect(screen.queryByRole('button', { name: 'Next' })).toBeNull()
  fireEvent.click(button('Set up myself'))
  expect(state.markSeen).toHaveBeenCalledOnce()
})

it('shows no intro on the screens that already say what they are for', async () => {
  // Hidden by the owner (launch review 2026-10-15): the Dashboard and Cash Flow.
  state.cashflowFill = 'empty'
  state.planSub = 'cashflow'
  const plan = render(<ScreenIntro screen="plan" onAskLucius={vi.fn()} />)
  await settle()
  expect(screen.queryByRole('dialog')).toBeNull()
  plan.unmount()
  for (const name of ['dashboard']) {
    resetGuidedForTests() // a new visit
    const view = render(<ScreenIntro screen={name} onAskLucius={vi.fn()} />)
    expect(screen.queryByRole('dialog')).toBeNull()
    view.unmount()
  }
})

it('leaves Outlook to its setup dialog until it can draw one', async () => {
  state.planSub = 'outlook'
  state.accounts = [{ id: 'acc_1' }]
  const { unmount } = render(<ScreenIntro screen="plan" onAskLucius={vi.fn()} />)
  await settle()
  expect(screen.queryByRole('dialog')).toBeNull()
  unmount()
  state.profile = { date_of_birth: '1990-06-01', fire_monthly_retirement_spending: 4000 }
  render(<ScreenIntro screen="plan" onAskLucius={vi.fn()} />)
  await settle()
  expect(screen.getByRole('dialog', { name: 'Will your money last?' })).toBeTruthy()
  expect(button('Try a what-if with Lucius')).toBeTruthy()
})

it('hands the screen its own setup message for Lucius', () => {
  const onAskLucius = vi.fn()
  render(<ScreenIntro screen="commitments" onAskLucius={onAskLucius} />)
  fireEvent.click(button('Set up with Lucius'))
  expect(onAskLucius).toHaveBeenCalledWith(expect.stringContaining('commitments'))
  expect(state.markSeen).toHaveBeenCalledOnce()
})

it('walks through a screen that already has data, and stays away once seen', () => {
  state.accounts = [{ id: 'acc_1' }]
  const onAskLucius = vi.fn()
  const { unmount } = render(<ScreenIntro screen="accounts" onAskLucius={onAskLucius} />)
  expect(screen.queryByRole('button', { name: 'Set up myself' })).toBeNull()
  fireEvent.click(button('Walk me through it'))
  expect(onAskLucius).toHaveBeenCalledWith(expect.stringContaining('my Accounts screen'))
  unmount()
  state.unseen = false
  render(<ScreenIntro screen="accounts" onAskLucius={vi.fn()} />)
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('waits for the data before deciding a screen is empty', () => {
  state.accounts = undefined
  render(<ScreenIntro screen="accounts" onAskLucius={vi.fn()} />)
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('offers budget setup until one is chosen, then a walkthrough', async () => {
  state.accounts = [{ id: 'acc_1' }]
  const { unmount } = render(<ScreenIntro screen="plan" onAskLucius={vi.fn()} />)
  await settle()
  expect(screen.getByRole('dialog', { name: 'Budget your way' })).toBeTruthy()
  expect(button('Set up myself')).toBeTruthy()
  unmount()
  resetGuidedForTests() // a new visit
  state.budget = {
    home_engine_id: 'proportional',
    engines: [{ id: 'proportional', configured: true }],
    intro_dismissed: false,
  }
  render(<ScreenIntro screen="plan" onAskLucius={vi.fn()} />)
  await settle()
  expect(button('Walk me through it')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Set up myself' })).toBeNull()
})

it('waits only while Lucius is mid-reply beside the screen', () => {
  state.replying = true
  const { unmount } = render(<ScreenIntro screen="accounts" luciusBeside onAskLucius={vi.fn()} />)
  expect(screen.queryByRole('dialog')).toBeNull()
  unmount()
  state.replying = false
  render(<ScreenIntro screen="accounts" luciusBeside onAskLucius={vi.fn()} />)
  expect(screen.getByRole('dialog')).toBeTruthy()
})

it('is not held back by a reply on a phone, where Lucius is never beside the screen', () => {
  state.replying = true
  render(<ScreenIntro screen="accounts" luciusBeside={false} onAskLucius={vi.fn()} />)
  expect(screen.getByRole('dialog')).toBeTruthy()
})

it('has nothing to say on a screen without an intro', () => {
  const { container } = render(<ScreenIntro screen="settings" onAskLucius={vi.fn()} />)
  expect(container.textContent).toBe('')
})
