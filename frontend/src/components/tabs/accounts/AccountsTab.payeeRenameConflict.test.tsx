// @vitest-environment jsdom
//
// Payee-name uniqueness, UI half. PayeesModal.editName already blocks the OBVIOUS
// duplicate client-side (`nameTaken` — exact, case-insensitive equality), but the
// server's rule is broader: uniqueness is on the NORMALIZED name and also spans the
// alias index (payee_registry._assert_name_available). So "Netflix, Inc." vs an
// existing "netflix inc", or a name matching another payee's alias, sails past the
// client check and comes back 409.
//
// changePayee used to `.then(() => L.refresh())` unconditionally: authFetch resolves
// on a 409 (it returns the raw Response and only intercepts 401), so the rejected
// rename was indistinguishable from a successful one — the row just reverted on the
// next refresh with no explanation. Same shape on addPayee's POST.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AccountsTab } from './AccountsTab'
import { clearQueryCache } from '../../../state/queryCache'
import type { ApiAccount, ApiPayee } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body, text: async () => JSON.stringify(body) } as Response
}

function conflict(detail: string): Response {
  return {
    ok: false,
    status: 409,
    json: async () => ({ detail }),
    text: async () => detail,
  } as Response
}

const ACCOUNT: ApiAccount = {
  id: 'acc-1',
  name: 'Checking',
  kind: 'asset',
  type: 'cash',
  currency: 'USD',
  balance: 1000,
  balance_base: 1000,
  cleared_balance: 1000,
  cleared_balance_base: 1000,
}

// "Netflix" and "Spotify" are distinct under the modal's exact-equality check, so
// renaming the latter to "Netflix, Inc." reaches the server — where it normalizes
// onto the former and 409s.
const PAYEES: ApiPayee[] = [
  { id: 'pay_netflix1', name: 'Netflix' },
  { id: 'pay_spotify1', name: 'Spotify' },
]

const CONFLICT_MSG = "a payee whose normalized name matches already exists: 'Netflix'"

function setupMocks() {
  mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([ACCOUNT]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url === '/api/payees' && opts?.method === 'POST')
      return Promise.resolve(conflict(CONFLICT_MSG))
    if (url.startsWith('/api/payees/') && opts?.method === 'PATCH')
      return Promise.resolve(conflict(CONFLICT_MSG))
    if (url === '/api/payees') return Promise.resolve(okJson({ payees: PAYEES }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url.startsWith('/api/transactions')) {
      return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
    }
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  setupMocks()
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1400 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

async function openPayeesModal(): Promise<void> {
  render(<AccountsTab showPayeesRoute />)
  await screen.findByDisplayValue('Netflix')
}

describe('Payees modal — a server-rejected rename is reported, not swallowed', () => {
  it('surfaces the 409 message when a rename collides on the normalized name', async () => {
    await openPayeesModal()

    // Select Spotify, then rename it onto Netflix's normalized form.
    fireEvent.click(screen.getByText('Spotify'))
    const nameBox = await screen.findByDisplayValue('Spotify')
    fireEvent.change(nameBox, { target: { value: 'Netflix, Inc.' } })

    await waitFor(() => {
      const patch = mockAuthFetch.mock.calls.find(
        ([u, o]) =>
          String(u).startsWith('/api/payees/') &&
          (o as RequestInit | undefined)?.method === 'PATCH',
      )
      expect(patch).toBeTruthy()
    })
    await screen.findByText(CONFLICT_MSG)
  })
})
