// @vitest-environment jsdom
//
// ZeroBasedPane — the setup experience Zero-Based never had. It was the
// only method with none: `configured` was hardcoded true and `draft()` returned None, so a
// first-time user picking the most recognisable budgeting method landed in an empty table.
//
// What it sets is TARGETS, not assignments. Assigning the money is the user's own ritual —
// that ritual IS zero-based budgeting — and must never be done on their behalf.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { clearQueryCache } from '../../../../../state/queryCache'
import { ZeroBasedPane } from './ZeroBasedPane'
import { _setSetupPromptHiddenForTests } from '../setup/SetupFlow'
import type { BudgetEngineDescriptor } from '../types'

const authFetch = vi.fn<typeof fetch>()
vi.mock('../../../../../api/client', () => ({
  authFetch: (...args: Parameters<typeof fetch>) => authFetch(...args),
}))
// The real budget table has its own heavy suite; this file is about the setup flow around it.
vi.mock('../../budget', () => ({
  BudgetTab: () => <div data-testid="stub-budget-tab">budget table</div>,
}))

// The "Set up <method>" prompt is parked (owner ask, 2026-09-15, commit 32d98bf8f) — KEEP IT
// PARKED for real users. Every test below except the one that pins the parked default lifts the
// switch through the test-only seam so the flow behind it (modal, draft, Apply, toasts) stays
// provably working while it's dark.
beforeEach(() => {
  _setSetupPromptHiddenForTests(false)
})
afterEach(() => {
  cleanup()
  clearQueryCache()
  authFetch.mockReset()
  _setSetupPromptHiddenForTests(true)
})

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

function descriptor(configured: boolean): BudgetEngineDescriptor {
  return {
    id: 'zero_based',
    name: 'Zero-Based',
    description: "Give every dollar a job until nothing's left unassigned.",
    question: 'What is still unassigned?',
    kind: 'configured',
    render: 'custom',
    is_home: true,
    configured,
    verbs: [],
  }
}

const DRAFT = {
  engine_id: 'zero_based',
  has_proposal: true,
  proposed_state: {
    targets: [{ envelope_id: 'env_rent', amount: 2150 }],
    rows: [
      {
        envelope_id: 'env_rent',
        label: 'Rent',
        low_confidence: false,
        suggested_amount: 2150,
        current_amount: 0,
        category_ids: ['cat_rent'],
      },
      {
        envelope_id: 'env_food',
        label: 'Food',
        low_confidence: false,
        suggested_amount: 600,
        current_amount: 0,
        category_ids: ['cat_food'],
      },
    ],
  },
}

function mockFetches(verbResponse: unknown = { applied: 2, failed: [] }) {
  authFetch.mockImplementation((input) => {
    const url = String(input)
    if (url.includes('/draft')) return json(DRAFT)
    if (url === '/api/planning/verb') return json(verbResponse)
    return json({})
  })
}

function renderPane(configured: boolean, toast = vi.fn()) {
  render(
    <ZeroBasedPane
      descriptor={descriptor(configured)}
      onConfiguredChange={vi.fn()}
      onAskLucius={vi.fn()}
      toast={toast}
    />,
  )
  return toast
}

describe('ZeroBasedPane', () => {
  it('a book that has never budgeted is offered setup instead of being left with an empty table', async () => {
    mockFetches()
    renderPane(false)
    expect(await screen.findByText('Set up Zero-Based')).toBeTruthy()
    // The real table still renders behind it — an empty budget IS the thing being set up.
    expect(screen.getByTestId('stub-budget-tab')).toBeTruthy()
  })

  it('a book that already budgets is never nagged', () => {
    mockFetches()
    renderPane(true)
    expect(screen.queryByText('Set up Zero-Based')).toBeNull()
    expect(screen.getByTestId('stub-budget-tab')).toBeTruthy()
  })

  it('setup opens in a modal and proposes a monthly target per envelope', async () => {
    mockFetches()
    renderPane(false)
    fireEvent.click(await screen.findByText('Set up Zero-Based'))
    fireEvent.click(await screen.findByText('Suggest from my spending'))

    await waitFor(() =>
      expect(screen.getByText('What does each envelope need every month?')).toBeTruthy(),
    )
    expect((screen.getByLabelText('Rent monthly target') as HTMLInputElement).value).toBe('2150')
    expect((screen.getByLabelText('Food monthly target') as HTMLInputElement).value).toBe('600')
  })

  it('Apply posts set_targets — never an assignment, which is the user’s own ritual', async () => {
    mockFetches()
    renderPane(false)
    fireEvent.click(await screen.findByText('Set up Zero-Based'))
    fireEvent.click(await screen.findByText('Suggest from my spending'))
    await waitFor(() => expect(screen.getByText('Rent')).toBeTruthy())

    fireEvent.click(screen.getByText('Apply'))

    await waitFor(() => {
      const call = authFetch.mock.calls.find(([u]) => String(u) === '/api/planning/verb')
      expect(call).toBeTruthy()
      const body = JSON.parse(String(call![1]?.body))
      expect(body.verb_id).toBe('set_targets')
      expect(body.params.targets).toEqual([
        { envelope_id: 'env_rent', amount: 2150 },
        { envelope_id: 'env_food', amount: 600 },
      ])
      // Never assigns money on the user's behalf.
      expect(body.verb_id).not.toBe('assign')
      expect(body.verb_id).not.toBe('auto_assign')
    })
  })

  it('a PARTIAL apply is said out loud, not dressed up as a clean success', async () => {
    mockFetches({ applied: 1, failed: [{ envelope_id: 'env_food', error: 'nope' }] })
    const toast = renderPane(false)
    fireEvent.click(await screen.findByText('Set up Zero-Based'))
    fireEvent.click(await screen.findByText('Suggest from my spending'))
    await waitFor(() => expect(screen.getByText('Rent')).toBeTruthy())

    fireEvent.click(screen.getByText('Apply'))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.stringContaining("1 couldn't be saved")),
    )
  })

  it('a clean apply points at the next step — targets alone are not a budget', async () => {
    mockFetches()
    const toast = renderPane(false)
    fireEvent.click(await screen.findByText('Set up Zero-Based'))
    fireEvent.click(await screen.findByText('Suggest from my spending'))
    await waitFor(() => expect(screen.getByText('Rent')).toBeTruthy())

    fireEvent.click(screen.getByText('Apply'))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith(expect.stringContaining('assign your money')),
    )
  })

  // The product's REAL default (every other test above overrides it via the seam). KEEP IT
  // PARKED means exactly this: whatever state the book is in, the prompt renders nothing.
  it('while parked, the product renders no setup prompt — not even for an unconfigured book', async () => {
    _setSetupPromptHiddenForTests(true)
    mockFetches()
    renderPane(false)
    await waitFor(() => expect(screen.getByTestId('stub-budget-tab')).toBeTruthy())
    expect(screen.queryByText('Set up Zero-Based')).toBeNull()
  })
})
