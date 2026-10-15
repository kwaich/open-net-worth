// @vitest-environment jsdom
//
// GenericEnginePane.test.tsx — the fallback setup flow for any budget engine with no bespoke
// frontend. None ships today, so the fixture is a made-up "Caps" engine with the one-list-param
// setup verb the generic pane drives. Mirrors proportional/ProportionalPane.setup.test.tsx's shape:
// the empty state renders with a working CTA, draft -> review -> apply calls POST
// /api/planning/verb with the engine's OWN declared verb (never hardcoded), and the "nothing
// to propose" path degrades to a message instead of crashing.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { clearQueryCache } from '../../../../state/queryCache'
import { GenericEnginePane } from './GenericEnginePane'
import type { BudgetEngineDescriptor } from './types'

const authFetch = vi.fn<typeof fetch>()
vi.mock('../../../../api/client', () => ({
  authFetch: (...args: Parameters<typeof fetch>) => authFetch(...args),
}))

afterEach(() => {
  cleanup()
  clearQueryCache()
  authFetch.mockReset()
})

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

function recapText(): string | null {
  return document.querySelector('.bgl-review__recap')?.textContent ?? null
}

const CAPS_UNCONFIGURED: BudgetEngineDescriptor = {
  id: 'caps',
  name: 'Caps',
  description: 'Cap the categories you tend to overspend.',
  question: 'Which caps are hit?',
  kind: 'configured',
  render: 'generic',
  is_home: false,
  configured: false,
  verbs: [
    {
      id: 'set_tracked',
      label: 'Choose which categories are capped',
      params: { category_ids: 'list of category ids or labels, required' },
    },
  ],
}

const CAPS_VIEW = {
  engine_id: 'caps',
  period: '2026-08',
  base_currency: 'USD',
  headline: { label: 'Caps hit', value: 0, unit: 'count', status: 'ok', help: null },
  groups: [],
  rows_shape: 'gauges',
  health: { verdict: 'good', reasons: [] },
  unmapped: null,
}

describe('GenericEnginePane — empty state', () => {
  it('renders "Set up Caps" with the descriptor description and both CTAs, never requests the ViewModel', async () => {
    authFetch.mockImplementation((input) => {
      const url = String(input)
      if (url.startsWith('/api/categories')) return json([])
      return json({})
    })
    render(<GenericEnginePane descriptor={CAPS_UNCONFIGURED} onConfiguredChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Set up Caps')).toBeTruthy())
    expect(screen.getByText(CAPS_UNCONFIGURED.description)).toBeTruthy()
    expect(screen.getByText('Suggest from my spending')).toBeTruthy()
    expect(screen.getByText('Set up manually')).toBeTruthy()
    expect(authFetch.mock.calls.some(([u]) => String(u).startsWith('/api/planning/view'))).toBe(
      false,
    )
  })
})

describe('GenericEnginePane — Suggest from my spending → review → Apply', () => {
  it("drafts, shows a toggleable review list, and applies with the engine's own declared verb", async () => {
    authFetch.mockImplementation((input, init) => {
      const url = String(input)
      if (url.startsWith('/api/categories')) return json([])
      if (url.startsWith('/api/planning/engines/caps/draft')) {
        return json({
          engine_id: 'caps',
          has_proposal: true,
          proposed_state: {
            category_ids: ['cat_dining'],
            rows: [
              { category_id: 'cat_groceries', label: 'Groceries', low_confidence: false },
              { category_id: 'cat_dining', label: 'Dining', low_confidence: true },
            ],
          },
        })
      }
      if (url === '/api/planning/verb' && init?.method === 'POST') {
        return json({ category_ids: ['cat_dining', 'cat_groceries'] })
      }
      if (url.startsWith('/api/planning/view')) return json(CAPS_VIEW)
      return json({})
    })
    render(<GenericEnginePane descriptor={CAPS_UNCONFIGURED} onConfiguredChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Suggest from my spending')).toBeTruthy())

    fireEvent.click(screen.getByText('Suggest from my spending'))
    expect(screen.getByText('Drafting your Caps setup…')).toBeTruthy()

    await waitFor(() => expect(screen.getByText('Groceries')).toBeTruthy())
    expect(screen.getByText('Dining')).toBeTruthy()
    expect(screen.getByText('low confidence')).toBeTruthy()
    expect(recapText()).toBe('1 of 2 selected') // only Dining pre-selected, from the draft's own list

    // The whole row is one <button role="switch"> now (not a checkbox <input>), so the state
    // lives on aria-checked rather than .checked — same behaviour, different mechanism.
    const groceriesSwitch = screen.getByRole('switch', { name: 'Include Groceries' })
    const diningSwitch = screen.getByRole('switch', { name: 'Include Dining' })
    expect(groceriesSwitch.getAttribute('aria-checked')).toBe('false')
    expect(diningSwitch.getAttribute('aria-checked')).toBe('true')
    fireEvent.click(groceriesSwitch) // include Groceries too
    expect(recapText()).toBe('2 of 2 selected')

    fireEvent.click(screen.getByText('Apply'))

    await waitFor(() =>
      expect(
        authFetch.mock.calls.some(
          ([u, init]) => String(u) === '/api/planning/verb' && init?.method === 'POST',
        ),
      ).toBe(true),
    )
    const verbCall = authFetch.mock.calls.find(([u]) => String(u) === '/api/planning/verb')!
    const body = JSON.parse(String(verbCall[1]?.body))
    expect(body.engine_id).toBe('caps')
    expect(body.verb_id).toBe('set_tracked') // discovered from descriptor.verbs, never hardcoded
    expect(new Set(body.params.category_ids)).toEqual(new Set(['cat_dining', 'cat_groceries']))

    // On success, the pane switches to the configured view.
    await waitFor(() => expect(screen.getByText('Caps hit')).toBeTruthy())
  })
})

describe('GenericEnginePane — Set up manually', () => {
  it('opens the review list with every expense category available, none selected, no draft call', async () => {
    authFetch.mockImplementation((input) => {
      const url = String(input)
      if (url.startsWith('/api/categories')) {
        return json([
          { id: 'cat_groceries', label: 'Groceries', kind: 'expense' },
          { id: 'cat_dining', label: 'Dining', kind: 'expense' },
        ])
      }
      return json({})
    })
    render(<GenericEnginePane descriptor={CAPS_UNCONFIGURED} onConfiguredChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Set up manually')).toBeTruthy())

    fireEvent.click(screen.getByText('Set up manually'))

    await waitFor(() => expect(screen.getByText('Groceries')).toBeTruthy())
    expect(recapText()).toBe('0 of 2 selected')
    expect(authFetch.mock.calls.some(([u]) => String(u).includes('/draft'))).toBe(false)
    expect(screen.queryByText(/best guess/)).toBeNull() // nothing was drafted, no honesty banner
  })
})

describe('GenericEnginePane — nothing to propose', () => {
  it('shows a message pointing at Set up manually of crashing', async () => {
    authFetch.mockImplementation((input) => {
      const url = String(input)
      if (url.startsWith('/api/categories')) return json([])
      if (url.startsWith('/api/planning/engines/caps/draft')) {
        return json({ engine_id: 'caps', has_proposal: false, proposed_state: null })
      }
      return json({})
    })
    render(<GenericEnginePane descriptor={CAPS_UNCONFIGURED} onConfiguredChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Suggest from my spending')).toBeTruthy())

    fireEvent.click(screen.getByText('Suggest from my spending'))

    await waitFor(() => expect(screen.getByText(/Nothing to draft yet/)).toBeTruthy())
    // Still on the empty state, not crashed — the setup CTAs are still there.
    expect(screen.getByText('Set up Caps')).toBeTruthy()
    expect(screen.getByText('Set up manually')).toBeTruthy()
  })
})

describe('GenericEnginePane — engine with no usable setup verb', () => {
  it('hides both setup CTAs instead of calling a verb that does not exist', async () => {
    authFetch.mockImplementation((input) => {
      const url = String(input)
      if (url.startsWith('/api/categories')) return json([])
      return json({})
    })
    const noVerbs: BudgetEngineDescriptor = { ...CAPS_UNCONFIGURED, verbs: [] }
    render(<GenericEnginePane descriptor={noVerbs} onConfiguredChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Set up Caps')).toBeTruthy())
    expect(screen.queryByText('Suggest from my spending')).toBeNull()
    expect(screen.queryByText('Set up manually')).toBeNull()
  })
})
