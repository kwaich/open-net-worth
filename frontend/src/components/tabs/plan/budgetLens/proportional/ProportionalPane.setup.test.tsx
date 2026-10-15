// @vitest-environment jsdom
//
// ProportionalPane.setup.test.tsx — an unconfigured 50/30/20 (acceptance C1: no fabricated
// ViewModel). Setting it up is the income → split onboarding (owner, 2026-09-25, replacing the
// category-mapping review): the pane only offers the way in.
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { clearQueryCache } from '../../../../../state/queryCache'
import { ProportionalPane } from './ProportionalPane'
import type { BudgetEngineDescriptor } from '../types'

const authFetch = vi.fn<typeof fetch>()
vi.mock('../../../../../api/client', () => ({
  authFetch: (...args: Parameters<typeof fetch>) => authFetch(...args),
}))

afterEach(() => {
  cleanup()
  clearQueryCache()
  authFetch.mockReset()
})

const UNCONFIGURED: BudgetEngineDescriptor = {
  id: 'proportional',
  name: '50/30/20',
  description: '',
  question: '',
  kind: 'derived',
  render: 'generic',
  is_home: false,
  configured: false,
  verbs: [],
}

describe('ProportionalPane — not set up yet (C1)', () => {
  it('offers the onboarding and never requests the ViewModel', async () => {
    authFetch.mockImplementation(() => Promise.resolve(new Response('{}', { status: 200 })))
    const onSetup = vi.fn()
    render(
      <ProportionalPane
        descriptor={UNCONFIGURED}
        toast={vi.fn()}
        onAskLucius={vi.fn()}
        onSetup={onSetup}
      />,
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Set up 50/30/20' }))
    expect(onSetup).toHaveBeenCalledTimes(1)
    // No category-mapping review any more — that screen is gone.
    expect(screen.queryByText(/Apply mapping|Suggest from my spending/)).toBeNull()
    expect(authFetch.mock.calls.some(([u]) => String(u).startsWith('/api/planning/view'))).toBe(
      false,
    )
  })
})
