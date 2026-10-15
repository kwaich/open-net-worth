// @vitest-environment jsdom
//
// The Budget tab's method machinery after the Budget Onboarding (cards) port (owner,
// 2026-09-25): ONE method menu in the masthead (switch, or compare read-only), no previewing a
// method you haven't switched to, and a new book's first-run chooser. The zero-based pane (<BudgetTab>) is stubbed — it has its own, much heavier suite.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { clearQueryCache } from '../../../../state/queryCache'
import { BudgetLensPane } from './BudgetLensPane'
import { MethodMenu } from './MethodMenu'
import { useBudgetLens } from './useBudgetLens'

// Mirrors Plan.tsx: the menu renders in the masthead while the pane renders below, both driven
// by one shared useBudgetLens.
function Harness({ onAskLucius = vi.fn() }: { onAskLucius?: (t: string) => void }) {
  const lens = useBudgetLens()
  return (
    <>
      <MethodMenu lens={lens} />
      <BudgetLensPane lens={lens} onAskLucius={onAskLucius} toast={vi.fn()} />
    </>
  )
}

const authFetch = vi.fn<typeof fetch>()
vi.mock('../../../../api/client', () => ({
  authFetch: (...args: Parameters<typeof fetch>) => authFetch(...args),
}))
vi.mock('../budget', () => ({
  BudgetTab: () => <div data-testid="stub-zero-based">zero-based stub</div>,
}))

afterEach(() => {
  cleanup()
  clearQueryCache()
  authFetch.mockReset()
})

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

const engine = (id: string, name: string, over: Record<string, unknown> = {}) => ({
  id,
  name,
  description: '',
  question: '',
  kind: id === 'proportional' ? 'derived' : 'configured',
  render: id === 'zero_based' ? 'custom' : 'generic',
  is_home: false,
  configured: false,
  verbs: [],
  ...over,
})

const EXISTING = {
  home_engine_id: 'zero_based',
  intro_dismissed: true,
  engines: [
    engine('zero_based', 'Zero-based', { is_home: true, configured: true }),
    engine('proportional', '50/30/20'),
  ],
}

const CONFIGURED_BOTH = {
  ...EXISTING,
  engines: [EXISTING.engines[0], engine('proportional', '50/30/20', { configured: true })],
}

function mockFetches(engines: unknown = EXISTING) {
  authFetch.mockImplementation((input) => {
    const url = String(input)
    if (url === '/api/planning/engines') return json(engines)
    if (url === '/api/planning/home-engine') return json(engines)
    if (url === '/api/planning/budget-lens-intro/dismiss') return json({ intro_dismissed: true })
    if (url === '/api/profile') return json({ monthly_net_income: 8000 })
    if (url === '/api/planning/verb') return json({ group_targets: {} })
    // 50/30/20's own draft: each Zero-Based group and its last-three-months spend.
    if (url.startsWith('/api/planning/engines/proportional/draft'))
      return json({
        engine_id: 'proportional',
        has_proposal: true,
        proposed_state: {
          group_targets: { grp_bills: 50, grp_fun: 20 },
          rows: [
            { group_id: 'grp_bills', name: 'Bills', avg_spend: 4000, percent: 50 },
            { group_id: 'grp_fun', name: 'Fun', avg_spend: 1600, percent: 20 },
          ],
        },
      })
    return json({})
  })
}

const posts = (path: string) =>
  authFetch.mock.calls.filter(
    ([u, init]) => String(u) === path && (init as RequestInit | undefined)?.method === 'POST',
  )

async function openMenu() {
  fireEvent.click(await screen.findByRole('button', { name: /Zero-Based/ }))
  return screen.getByRole('menu')
}

describe('the method menu', () => {
  it('names the current method and offers exactly the two methods plus Compare', async () => {
    mockFetches()
    render(<Harness />)
    const menu = await openMenu()
    const items = within(menu).getAllByRole('menuitemradio')
    expect(items.map((i) => i.textContent)).toEqual(['50/30/20', 'Zero-Based'])
    expect(
      within(menu).getByRole('menuitemradio', { name: 'Zero-Based' }).getAttribute('aria-checked'),
    ).toBe('true')
    expect(within(menu).getByRole('menuitem', { name: 'Compare methods…' })).toBeTruthy()
  })

  it('switching to a method set up before asks first, then promotes it', async () => {
    mockFetches(CONFIGURED_BOTH)
    render(<Harness />)
    fireEvent.click(within(await openMenu()).getByRole('menuitemradio', { name: '50/30/20' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Switch back to 50/30/20?')).toBeTruthy()
    expect(dialog.textContent).toMatch(/split and caps are as you left them/)
    expect(posts('/api/planning/home-engine')).toHaveLength(0)

    fireEvent.click(within(dialog).getByRole('button', { name: 'Switch' }))
    await waitFor(() => expect(posts('/api/planning/home-engine')).toHaveLength(1))
    const [, init] = posts('/api/planning/home-engine')[0]!
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({ engine_id: 'proportional' })
  })

  it('Cancel on the switch POSTs nothing', async () => {
    mockFetches(CONFIGURED_BOTH)
    render(<Harness />)
    fireEvent.click(within(await openMenu()).getByRole('menuitemradio', { name: '50/30/20' }))
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Cancel' }),
    )
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(posts('/api/planning/home-engine')).toHaveLength(0)
  })

  it('a 50/30/20 never set up is onboarded BEFORE it becomes the method — income, shares, Start budgeting', async () => {
    mockFetches()
    render(<Harness />)
    fireEvent.click(within(await openMenu()).getByRole('menuitemradio', { name: '50/30/20' }))
    const confirm = await screen.findByRole('dialog')
    expect(confirm.textContent).toMatch(/You’ll set your income and each group’s share next/)
    fireEvent.click(within(confirm).getByRole('button', { name: 'Switch' }))

    // The onboarding, pre-filled from the Profile — and still nothing promoted.
    expect(await screen.findByText('What’s your monthly net income?')).toBeTruthy()
    expect(screen.getByText(/From your Profile/)).toBeTruthy()
    expect(posts('/api/planning/home-engine')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    // Each Zero-Based group, starting at its share of recent spend (4000 of 8000 is 50%).
    expect(await screen.findByText('How much should each group get?')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Raise Fun' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start budgeting' }))

    await waitFor(() => expect(posts('/api/planning/home-engine')).toHaveLength(1))
    const profile = authFetch.mock.calls.find(
      ([u, init]) => String(u) === '/api/profile' && (init as RequestInit)?.method === 'PUT',
    )
    expect(JSON.parse(String((profile![1] as RequestInit).body))).toEqual({
      monthly_net_income: 8000,
    })
    // `configure` saves each group's share — and it must land BEFORE the switch, which the
    // backend refuses for a 50/30/20 that was never configured.
    const [configure] = posts('/api/planning/verb')
    expect(JSON.parse(String((configure![1] as RequestInit).body))).toEqual({
      engine_id: 'proportional',
      verb_id: 'configure',
      params: { group_targets: { grp_bills: 50, grp_fun: 25 } },
    })
    const order = authFetch.mock.calls.map(([u]) => String(u))
    expect(order.indexOf('/api/planning/verb')).toBeLessThan(
      order.indexOf('/api/planning/home-engine'),
    )
  })

  it('a group unticked in onboarding starts outside the split — its share cleared', async () => {
    mockFetches()
    render(<Harness />)
    fireEvent.click(within(await openMenu()).getByRole('menuitemradio', { name: '50/30/20' }))
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Switch' }),
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('How much should each group get?')).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Count Fun in the split' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start budgeting' }))
    await waitFor(() => expect(posts('/api/planning/home-engine')).toHaveLength(1))
    const [configure] = posts('/api/planning/verb')
    expect(JSON.parse(String((configure![1] as RequestInit).body))).toEqual({
      engine_id: 'proportional',
      verb_id: 'configure',
      params: { group_targets: { grp_bills: 50 }, excluded_groups: ['grp_fun'] },
    })
  })

  it('a new book with no income yet still lists every spending group to give a share', async () => {
    mockFetches()
    const base = authFetch.getMockImplementation()!
    authFetch.mockImplementation((input, init) => {
      const url = String(input)
      // No income to measure against → the draft proposes nothing.
      if (url.startsWith('/api/planning/engines/proportional/draft'))
        return json({ engine_id: 'proportional', has_proposal: false, proposed_state: null })
      if (url === '/api/planning/groups')
        return json([
          { id: 'grp_bills', name: 'Bills' },
          { id: 'grp_cc', name: 'Credit Card Payments', synced: true },
          { id: 'grp_fun', name: 'Fun' },
        ])
      return base(input, init)
    })
    render(<Harness />)
    fireEvent.click(within(await openMenu()).getByRole('menuitemradio', { name: '50/30/20' }))
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Switch' }),
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Continue' }))
    expect(await screen.findByText('How much should each group get?')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Raise Bills' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Raise Fun' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Raise Credit Card Payments' })).toBeNull()
  })

  it('closing the onboarding changes nothing', async () => {
    mockFetches()
    render(<Harness />)
    fireEvent.click(within(await openMenu()).getByRole('menuitemradio', { name: '50/30/20' }))
    fireEvent.click(
      within(await screen.findByRole('dialog')).getByRole('button', { name: 'Switch' }),
    )
    await screen.findByText('What’s your monthly net income?')
    fireEvent.click(screen.getByRole('button', { name: /close/i }))
    await waitFor(() => expect(screen.queryByText('What’s your monthly net income?')).toBeNull())
    expect(posts('/api/planning/home-engine')).toHaveLength(0)
    expect(screen.getByTestId('stub-zero-based')).toBeTruthy()
  })

  it('Compare methods… is read-only: both cards, the current one marked, no way to switch from it', async () => {
    mockFetches()
    render(<Harness />)
    fireEvent.click(within(await openMenu()).getByRole('menuitem', { name: 'Compare methods…' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Compare budgeting methods')).toBeTruthy()
    expect(within(dialog).getByText('50/30/20')).toBeTruthy()
    expect(within(dialog).getByText('Zero-Based')).toBeTruthy()
    expect(within(dialog).getByText('Current')).toBeTruthy()
    expect(within(dialog).queryByRole('button', { name: /Switch|Start|Preview/ })).toBeNull()
  })

  it('the Budget tab shows ONLY the current method — no preview of another', async () => {
    mockFetches()
    render(<Harness />)
    expect(await screen.findByTestId('stub-zero-based')).toBeTruthy()
    expect(screen.queryByText(/Previewing/)).toBeNull()
  })
})

describe('a new book: the first-run chooser', () => {
  const NEW_BOOK = {
    home_engine_id: 'proportional',
    intro_dismissed: false,
    engines: [engine('zero_based', 'Zero-based'), engine('proportional', '50/30/20')],
  }

  it('replaces the pane, 50/30/20 picked by default', async () => {
    mockFetches(NEW_BOOK)
    render(<Harness />)
    expect(await screen.findByText('How do you want to budget?')).toBeTruthy()
    const cards = screen.getAllByRole('radio')
    expect(cards.map((c) => c.getAttribute('aria-checked'))).toEqual(['true', 'false'])
    expect(screen.getByRole('button', { name: /Start with 50\/30\/20/ })).toBeTruthy()
  })

  it('picking a card and Start makes it home straight away — nothing to lose, no confirmation', async () => {
    mockFetches(NEW_BOOK)
    render(<Harness />)
    fireEvent.click((await screen.findAllByRole('radio'))[1]!)
    fireEvent.click(screen.getByRole('button', { name: /Start with Zero-Based/ }))
    await waitFor(() => expect(posts('/api/planning/home-engine')).toHaveLength(1))
    await waitFor(() => expect(posts('/api/planning/budget-lens-intro/dismiss')).toHaveLength(1))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('Start with 50/30/20 opens its onboarding; finishing makes it home and closes the chooser', async () => {
    mockFetches(NEW_BOOK)
    render(<Harness />)
    fireEvent.click(await screen.findByRole('button', { name: /Start with 50\/30\/20/ }))
    expect(await screen.findByText('What’s your monthly net income?')).toBeTruthy()
    expect(posts('/api/planning/home-engine')).toHaveLength(0)
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Start budgeting' }))
    await waitFor(() => expect(posts('/api/planning/home-engine')).toHaveLength(1))
    await waitFor(() => expect(posts('/api/planning/budget-lens-intro/dismiss')).toHaveLength(1))
  })

  it('a fit picks the method that suits it', async () => {
    mockFetches(NEW_BOOK)
    render(<Harness />)
    fireEvent.click(await screen.findByRole('button', { name: /Account for every dollar/ }))
    expect(screen.getAllByRole('radio').map((c) => c.getAttribute('aria-checked'))).toEqual([
      'false',
      'true',
    ])
    expect(screen.getByText('Fits you')).toBeTruthy()
  })

  it('offers one concise Lucius choice prompt on a new book', async () => {
    mockFetches(NEW_BOOK)
    const ask = vi.fn()
    render(<Harness onAskLucius={ask} />)
    fireEvent.click(await screen.findByRole('button', { name: /Help me choose/ }))
    expect(ask).toHaveBeenCalledWith('Help me choose a budgeting method')
    expect(screen.getByText('The figures below are examples, not your spending.')).toBeTruthy()
  })
})
