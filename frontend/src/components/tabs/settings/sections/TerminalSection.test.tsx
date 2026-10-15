// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))
vi.mock('./TerminalView', () => ({
  TerminalView: ({ onEnded }: { onEnded: (r: 'ended') => void }) => (
    <button onClick={() => onEnded('ended')}>fake terminal</button>
  ),
}))
const startGatewayReauth = vi.hoisted(() => vi.fn())
vi.mock('../../../../utils/gatewayReauth', async (orig) => ({
  ...(await orig<typeof import('../../../../utils/gatewayReauth')>()),
  startGatewayReauth,
}))

import { authFetch } from '../../../../api/client'
import { TerminalSection } from './TerminalSection'

const mockAuthFetch = vi.mocked(authFetch)

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})
afterEach(cleanup)

describe('TerminalSection', () => {
  it('shows the ownership notice and opens a terminal only when asked', async () => {
    mockAuthFetch.mockImplementation(async () =>
      json({ available: true, reason: null, in_use: false }),
    )
    render(<TerminalSection toast={vi.fn()} gatewayMode />)

    expect(screen.getByText(/your own agreement with their provider/i)).toBeTruthy()
    const open = await screen.findByRole('button', { name: 'Open terminal' })
    expect(screen.queryByText('fake terminal')).toBeNull()
    fireEvent.click(open)
    expect(await screen.findByText('fake terminal')).toBeTruthy()
    fireEvent.click(screen.getByText('fake terminal'))
    expect(await screen.findByRole('button', { name: 'Open terminal' })).toBeTruthy()
  })

  it('explains when the terminal is switched off', async () => {
    mockAuthFetch.mockImplementation(async () =>
      json({ available: false, reason: 'disabled', in_use: false }),
    )
    render(<TerminalSection toast={vi.fn()} />)
    expect(await screen.findByText(/switched off on this server/i)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Open terminal' })).toBeNull()
  })

  it('asks for a recent sign-in when the session is too old', async () => {
    mockAuthFetch.mockImplementation(async () =>
      json({ code: 'fresh_session_required', reauth: 'https://cloud.example/r' }, 401),
    )
    render(<TerminalSection toast={vi.fn()} gatewayMode />)
    fireEvent.click(await screen.findByRole('button', { name: 'Sign in again' }))
    await waitFor(() =>
      expect(startGatewayReauth).toHaveBeenCalledWith(
        'https://cloud.example/r',
        undefined,
        undefined,
        true,
      ),
    )
  })

  it('inside the desktop app the fresh sign-in goes through the app, not a navigation', async () => {
    mockAuthFetch.mockImplementation(async () =>
      json({ code: 'fresh_session_required', reauth: 'https://cloud.example/r' }, 401),
    )
    window.history.replaceState(null, '', '/settings/terminal')
    const requestFreshSignIn = vi.fn().mockResolvedValue(undefined)
    window.onwDesktopClient = { version: 1, requestFreshSignIn } as never
    try {
      render(<TerminalSection toast={vi.fn()} gatewayMode />)
      fireEvent.click(await screen.findByRole('button', { name: 'Sign in again' }))
      await waitFor(() => expect(requestFreshSignIn).toHaveBeenCalledWith('/settings/terminal'))
      expect(startGatewayReauth).not.toHaveBeenCalled()
    } finally {
      delete window.onwDesktopClient
    }
  })

  it('says so when the desktop app cannot start the sign-in', async () => {
    mockAuthFetch.mockImplementation(async () => json({ code: 'fresh_session_required' }, 401))
    window.onwDesktopClient = {
      version: 1,
      requestFreshSignIn: vi.fn().mockRejectedValue(new Error('no')),
    } as never
    try {
      render(<TerminalSection toast={vi.fn()} gatewayMode />)
      fireEvent.click(await screen.findByRole('button', { name: 'Sign in again' }))
      expect(await screen.findByText(/could not start the sign-in/i)).toBeTruthy()
    } finally {
      delete window.onwDesktopClient
    }
  })
})
