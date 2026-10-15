// @vitest-environment jsdom
//
// The zero-accounts state keeps the first decision focused: a manual account or Lucius.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AccountsTab } from './AccountsTab'
import { SCREEN_INTROS } from '../../intro/screenIntros'
import { takeSendIntent } from '../../../state/luciusSendIntent'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

/** A book with no accounts at all — the state this file is about. */
function setupEmpty(hasAi: boolean) {
  mockAuthFetch.mockImplementation((url: string) => {
    if (url === '/api/model-config')
      return Promise.resolve(okJson({ provider: hasAi ? 'claude_cli' : '' }))
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url.startsWith('/api/transactions')) {
      return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
    }
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1400 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('AccountsTab — focused zero-accounts state', () => {
  it('offers only manual account creation, then Lucius', async () => {
    setupEmpty(true)
    render(<AccountsTab onAskLucius={vi.fn()} />)

    await screen.findByRole('button', { name: 'Add with Lucius' })
    expect(screen.getByRole('button', { name: 'Add an account myself' })).toBeTruthy()
    expect(screen.queryByText(/Connect a bank or broker/i)).toBeNull()
    expect(screen.queryByText(/Import statement files/i)).toBeNull()
    expect(screen.queryByText(/Restore a backup/i)).toBeNull()
    expect(screen.queryByText(/YNAB or Actual Budget/i)).toBeNull()
    expect(screen.queryByText('Start with')).toBeNull()
  })

  it('opens the add-account modal from the manual route', async () => {
    setupEmpty(true)
    render(<AccountsTab />)

    fireEvent.click(await screen.findByRole('button', { name: 'Add an account myself' }))
    // The modal that the old single button opened — the manual path must still work.
    await waitFor(() => expect(document.querySelector('.ui-modal, [role="dialog"]')).toBeTruthy())
  })

  it('sends the account-setup prompt to Lucius in a fresh conversation', async () => {
    setupEmpty(true)
    const onAskLucius = vi.fn()
    render(<AccountsTab onAskLucius={onAskLucius} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Add with Lucius' }))

    expect(onAskLucius).toHaveBeenCalledWith(SCREEN_INTROS.accounts.lucius)
    expect(takeSendIntent()).toBe(true)
  })
})
