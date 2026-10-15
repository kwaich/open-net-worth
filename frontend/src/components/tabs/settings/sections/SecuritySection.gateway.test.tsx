// @vitest-environment jsdom
//
// Gateway mode: the proxy owns sign-in, so the password and session controls are gone — but
// phone Shortcuts and MCP clients still authenticate with the app's own API token, so that
// panel stays.

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as client from '../../../../api/client'
import { clearQueryCache } from '../../../../state/queryCache'
import { SecuritySection } from './SecuritySection'

beforeEach(() => {
  clearQueryCache()
  vi.spyOn(client, 'authFetch').mockImplementation(async (input) => {
    const body = String(input).includes('api-token')
      ? { token: 'synthetic-token', configured: true }
      : { session_ttl: 2592000, trust_loopback: true, auth_disabled: false }
    return { ok: true, status: 200, json: async () => body } as Response
  })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('SecuritySection in gateway mode', () => {
  it('keeps the API token controls and hides password and auth behaviour', async () => {
    render(<SecuritySection toast={vi.fn()} gatewayMode />)

    expect(await screen.findByText('API token')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Reveal' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Rotate' })).toBeTruthy()
    expect(screen.queryByText('Password')).toBeNull()
    expect(screen.queryByText('Change password')).toBeNull()
    expect(screen.queryByText('Auth behaviour')).toBeNull()
    expect(screen.queryByText(/disable auth/i)).toBeNull()
  })

  it('outside gateway mode still shows every panel', async () => {
    render(<SecuritySection toast={vi.fn()} />)

    expect(await screen.findByText('API token')).toBeTruthy()
    expect(screen.getByText('Password')).toBeTruthy()
    expect(screen.getByText('Auth behaviour')).toBeTruthy()
  })
})
