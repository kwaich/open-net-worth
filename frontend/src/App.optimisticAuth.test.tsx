// @vitest-environment jsdom
//
// Optimistic auth (2026-07-27): `auth` used to start null on every mount, so the app was
// gated behind a /api/auth/status round trip and rendered a blocking splash first. On iOS
// that is the reopen experience — a standalone PWA is torn down when backgrounded, so every
// reopen is a cold start and the splash is what you see.
//
// These pin the SAFE part of rendering optimistically: the cached value only decides what
// paints first, and every way it can be wrong still self-corrects.

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DesktopBridge } from '../../desktop/src/contracts'

const authFetch = vi.hoisted(() => vi.fn())
const setUnauthorizedHandler = vi.hoisted(() => vi.fn())
vi.mock('./api/client', () => ({ authFetch, setUnauthorizedHandler }))
const startGatewayReauth = vi.hoisted(() => vi.fn())
const reauthState = vi.hoisted(() => ({ on: false }))
vi.mock('./utils/gatewayReauth', () => ({
  startGatewayReauth,
  subscribeReauthing: () => () => {},
  isReauthing: () => reauthState.on,
  setGatewayPortal: () => {},
}))
vi.mock('./router', () => ({ router: {} }))
vi.mock('@tanstack/react-router', () => ({
  RouterProvider: () => <div data-testid="app-shell">APP</div>,
}))
vi.mock('./components/Login', () => ({ Login: () => <div data-testid="login">LOGIN</div> }))
vi.mock('./registerSW.tsx', () => ({ useServiceWorkerUpdate: () => {} }))
vi.mock('./brand-context', () => ({ useBrand: () => ({ app_name: 'Open Net Worth' }) }))

const AUTH_CACHE_KEY = 'onw-auth-status-v2'

function jsonResponse(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) })
}

let App: typeof import('./App').default

beforeEach(async () => {
  localStorage.clear()
  delete window.onwDesktop
  window.history.replaceState(null, '', '/')
  authFetch.mockReset()
  setUnauthorizedHandler.mockReset()
  startGatewayReauth.mockReset()
  reauthState.on = false
  vi.resetModules()
  App = (await import('./App')).default
})

afterEach(() => {
  cleanup() // unmount between cases — otherwise renders stack and queries match twice
  localStorage.clear()
  delete window.onwDesktop
})

describe('App optimistic auth', () => {
  it.each([
    ['/setup/folder', 'Getting your setup ready…'],
    ['/setup/restore', 'Preparing to restore your book…'],
    ['/', 'Opening your book…'],
  ])('keeps the desktop logo and stage message while access is checked at %s', (path, label) => {
    window.onwDesktop = { version: 1 } as DesktopBridge
    window.history.replaceState(null, '', path)
    authFetch.mockReturnValue(new Promise(() => {}))

    render(<App />)

    const status = screen.getByRole('status', { name: label })
    expect(status.querySelector('img')?.getAttribute('src')).toContain('/logo-mark.svg')
    expect(screen.queryByText('OPEN NET WORTH')).toBeNull()
    expect(screen.queryByTestId('app-shell')).toBeNull()
  })

  it('shows the blocking splash on a genuine first visit (nothing cached)', async () => {
    let resolve!: (v: unknown) => void
    authFetch.mockReturnValue(new Promise((r) => (resolve = r)))

    render(<App />)
    // Nothing better to show yet — the splash is correct here.
    expect(screen.getByText('OPEN NET WORTH')).toBeTruthy()
    expect(screen.queryByTestId('app-shell')).toBeNull()

    resolve({ ok: true, json: () => Promise.resolve({ required: false, authenticated: true }) })
    await waitFor(() => expect(screen.getByTestId('app-shell')).toBeTruthy())
  })

  it('renders the app immediately from cache, with NO splash, while re-verifying', async () => {
    localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify({ required: true, authenticated: true }))
    // Status never resolves — proves the first paint does not wait on the network.
    authFetch.mockReturnValue(new Promise(() => {}))

    render(<App />)

    expect(screen.getByTestId('app-shell')).toBeTruthy()
    expect(screen.queryByText('OPEN NET WORTH')).toBeNull()
  })

  it('persists the resolved status so the next mount can skip the splash', async () => {
    authFetch.mockReturnValue(jsonResponse({ required: true, authenticated: true }))

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('app-shell')).toBeTruthy())

    expect(JSON.parse(localStorage.getItem(AUTH_CACHE_KEY)!)).toEqual({
      required: true,
      authenticated: true,
    })
  })

  it('says a server without a password yet must be set up on its own machine, instead of a login form', async () => {
    authFetch.mockReturnValue(jsonResponse({ required: false, authenticated: false }))

    render(<App />)
    await waitFor(() => expect(screen.getByText(/set a password first/i)).toBeTruthy())
    expect(screen.getByText(/on the computer it runs on/i)).toBeTruthy()
    expect(screen.queryByTestId('login')).toBeNull()
    expect(screen.queryByTestId('app-shell')).toBeNull()
  })

  it('corrects a STALE cached "authenticated" once the server says otherwise', async () => {
    localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify({ required: true, authenticated: true }))
    authFetch.mockReturnValue(jsonResponse({ required: true, authenticated: false }))

    render(<App />)
    // Optimistic first paint…
    expect(screen.getByTestId('app-shell')).toBeTruthy()
    // …then the real answer wins.
    await waitFor(() => expect(screen.getByTestId('login')).toBeTruthy())
    expect(JSON.parse(localStorage.getItem(AUTH_CACHE_KEY)!).authenticated).toBe(false)
  })

  it('shows Login immediately (no app flash) when the cache says logged out', () => {
    localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify({ required: true, authenticated: false }))
    authFetch.mockReturnValue(new Promise(() => {}))

    render(<App />)

    expect(screen.getByTestId('login')).toBeTruthy()
    expect(screen.queryByTestId('app-shell')).toBeNull()
  })

  it('a corrupt cached value falls back to the splash instead of crashing', () => {
    localStorage.setItem(AUTH_CACHE_KEY, '{"required":"yes-please"')
    authFetch.mockReturnValue(new Promise(() => {}))

    render(<App />)

    expect(screen.getByText('OPEN NET WORTH')).toBeTruthy()
  })

  it('a network failure never downgrades or persists over a cached status', async () => {
    localStorage.setItem(AUTH_CACHE_KEY, JSON.stringify({ required: true, authenticated: false }))
    authFetch.mockRejectedValue(new Error('offline'))

    render(<App />)
    await waitFor(() => expect(authFetch).toHaveBeenCalled())

    // The permissive offline fallback must NOT overwrite a real cached status —
    // otherwise a protected instance would cache "open" and flash the app next reopen.
    expect(screen.getByTestId('login')).toBeTruthy()
    expect(JSON.parse(localStorage.getItem(AUTH_CACHE_KEY)!)).toEqual({
      required: true,
      authenticated: false,
    })
  })

  it('gateway mode renders the app, never Login, and remembers the mode', async () => {
    authFetch.mockReturnValue(
      jsonResponse({
        required: false,
        authenticated: true,
        mode: 'gateway',
        gateway: { email: 'a@example.com' },
      }),
    )

    render(<App />)

    await waitFor(() => expect(screen.getByTestId('app-shell')).toBeTruthy())
    expect(screen.queryByTestId('login')).toBeNull()
    expect(JSON.parse(localStorage.getItem(AUTH_CACHE_KEY)!)).toEqual({
      required: false,
      authenticated: true,
      mode: 'gateway',
    })
  })

  it('a passwordless demo renders the app, never Login, and remembers the mode', async () => {
    authFetch.mockReturnValue(jsonResponse({ required: false, authenticated: true, mode: 'demo' }))

    render(<App />)

    await waitFor(() => expect(screen.getByTestId('app-shell')).toBeTruthy())
    expect(screen.queryByTestId('login')).toBeNull()
    expect(JSON.parse(localStorage.getItem(AUTH_CACHE_KEY)!)).toEqual({
      required: false,
      authenticated: true,
      mode: 'demo',
    })
  })

  it('a demo visitor who bounced off the old password screen is not shown Login on return', () => {
    // What the previous build left behind: logged out, under the previous cache key.
    localStorage.setItem(
      'onw-auth-status',
      JSON.stringify({ required: true, authenticated: false }),
    )
    authFetch.mockReturnValue(new Promise(() => {}))

    render(<App />)

    expect(screen.queryByTestId('login')).toBeNull()
    expect(screen.getByText('OPEN NET WORTH')).toBeTruthy()
  })

  it('gateway mode answers a 401 by signing back in through the proxy, never the logged-out state', async () => {
    authFetch.mockReturnValue(
      jsonResponse({ required: false, authenticated: true, mode: 'gateway' }),
    )

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('app-shell')).toBeTruthy())

    const on401 = setUnauthorizedHandler.mock.calls.find(([fn]) => fn)![0] as (u?: string) => void
    act(() => on401('https://cloud.example/reauth'))

    expect(startGatewayReauth).toHaveBeenCalledWith('https://cloud.example/reauth')
    expect(screen.queryByTestId('login')).toBeNull()
    expect(JSON.parse(localStorage.getItem(AUTH_CACHE_KEY)!).authenticated).toBe(true)
  })

  it('says it is signing the person back in while the re-sign-in runs', async () => {
    reauthState.on = true
    authFetch.mockReturnValue(
      jsonResponse({ required: false, authenticated: true, mode: 'gateway' }),
    )

    render(<App />)

    expect(screen.getByText('Signing you back in…')).toBeTruthy()
    expect(screen.queryByTestId('app-shell')).toBeNull()
  })

  it('outside gateway mode a 401 still shows Login and never reloads', async () => {
    authFetch.mockReturnValue(jsonResponse({ required: true, authenticated: true }))

    render(<App />)
    await waitFor(() => expect(screen.getByTestId('app-shell')).toBeTruthy())

    const on401 = setUnauthorizedHandler.mock.calls.find(([fn]) => fn)![0] as () => void
    act(() => on401())

    await waitFor(() => expect(screen.getByTestId('login')).toBeTruthy())
    expect(startGatewayReauth).not.toHaveBeenCalled()
  })

  it('shows a retry screen, not a forever splash, when the status request times out', async () => {
    authFetch.mockRejectedValueOnce(new DOMException('signal timed out', 'TimeoutError'))

    render(<App />)
    await screen.findByText(/can.t reach open net worth/i)
    expect(screen.queryByTestId('app-shell')).toBeNull()

    authFetch.mockReturnValue(jsonResponse({ required: false, authenticated: true }))
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))
    await waitFor(() => expect(screen.getByTestId('app-shell')).toBeTruthy())
    const firstInit = authFetch.mock.calls[0]?.[1] as RequestInit | undefined
    expect(firstInit?.signal).toBeInstanceOf(AbortSignal)
  })

  describe('"Set a password first" retry', () => {
    const NONE = { required: false, authenticated: false }

    it('says the server still has no password when the re-check comes back unchanged', async () => {
      authFetch.mockReturnValue(jsonResponse(NONE))
      render(<App />)
      const retry = await screen.findByRole('button', { name: /I.ve set it, sign in/i })
      expect(screen.queryByText(/still no password/i)).toBeNull()

      fireEvent.click(retry)

      expect(
        await screen.findByText(
          'Still no password on that server. Set one there first, then try again.',
        ),
      ).toBeTruthy()
    })

    it('leaves the card for the login form once a password exists, with no warning', async () => {
      authFetch.mockReturnValueOnce(jsonResponse(NONE))
      render(<App />)
      const retry = await screen.findByRole('button', { name: /I.ve set it, sign in/i })

      authFetch.mockReturnValue(jsonResponse({ required: true, authenticated: false }))
      fireEvent.click(retry)

      await waitFor(() => expect(screen.getByTestId('login')).toBeTruthy())
      expect(screen.queryByText(/still no password/i)).toBeNull()
    })

    it('says the server could not be reached rather than claiming there is no password', async () => {
      authFetch.mockReturnValueOnce(jsonResponse(NONE))
      render(<App />)
      const retry = await screen.findByRole('button', { name: /I.ve set it, sign in/i })

      authFetch.mockRejectedValue(new TypeError('Failed to fetch'))
      fireEvent.click(retry)

      expect(await screen.findByText(/could not reach the server/i)).toBeTruthy()
      expect(screen.queryByText(/still no password/i)).toBeNull()
    })
  })
})
