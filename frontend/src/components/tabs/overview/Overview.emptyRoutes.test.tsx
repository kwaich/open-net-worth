// @vitest-environment jsdom
//
// The Overview empty state is where a brand-new user actually lands. It is the shared
// zero-accounts view: one way in of its own, and Lucius's purple button beside it.

import { cleanup, render, screen, fireEvent } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { Overview } from './Overview'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

vi.mock('./useDashboardLayout', () => ({
  useDashboardLayout: () => ({
    loading: false,
    error: null,
    state: { activeViewId: 'overview', views: [] },
    views: [],
    activeView: { viewId: 'overview', name: 'Overview', widgets: [] },
    activeViewId: 'overview',
    isDirty: false,
    pickerDefinitions: [],
    snapshot: vi.fn(),
    restoreSnapshot: vi.fn(),
    setActiveViewId: vi.fn(),
    addView: vi.fn(),
    renameView: vi.fn(),
    deleteView: vi.fn(),
    reorderViews: vi.fn(),
    addWidgetWithConfig: vi.fn(),
    addSection: vi.fn(),
  }),
}))

describe('Overview empty state', () => {
  beforeAll(() => {
    global.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as unknown as typeof ResizeObserver
  })

  afterEach(cleanup)

  it('offers adding an account and Lucius, nothing else', () => {
    render(<Overview rows={[]} onChatSuggest={vi.fn()} />)

    expect(screen.getAllByRole('button').map((b) => b.textContent)).toEqual([
      'Add an account myself',
      'Add with Lucius',
    ])
  })

  it('routes "Add an account myself" to Accounts', () => {
    const onNavigate = vi.fn()
    render(<Overview rows={[]} onNavigate={onNavigate} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add an account myself' }))
    expect(onNavigate).toHaveBeenCalledWith('accounts')
  })

  it('sends the setup message to Lucius', () => {
    const onChatSuggest = vi.fn()
    render(<Overview rows={[]} onChatSuggest={onChatSuggest} />)

    fireEvent.click(screen.getByRole('button', { name: 'Add with Lucius' }))
    expect(onChatSuggest).toHaveBeenCalledWith('Help me set up my accounts')
  })
})
