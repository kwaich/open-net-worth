// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const SETUP_CODE = 'https://box.example.com|tok_secret_value'

const authFetch = vi.fn(async (path: string, init?: RequestInit) => {
  const json = (body: unknown) =>
    new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })
  if (path === '/api/shortcuts')
    return json({
      base_url: 'https://box.example.com',
      capture_path: '/api/capture',
      shortcuts: [
        {
          slug: 'log-apple-pay',
          name: 'Log a spend',
          summary: 'From an Apple Pay tap.',
          asks: 'setup code',
          signed: true,
        },
      ],
    })
  if (path.startsWith('/api/shortcuts/setup-code')) return json({ setup_code: SETUP_CODE })
  if (path === '/api/auth/api-token') return json({ token: 'tok_secret_value' })
  if (path.startsWith('/api/shortcuts/setup?'))
    return json({
      setup_code: SETUP_CODE,
      shortcuts: [
        {
          slug: 'log-apple-pay',
          name: 'Log a spend',
          summary: 'From an Apple Pay tap.',
          asks: 'setup code',
          install_url: '/api/shortcuts/file?t=abc&slug=log-apple-pay',
        },
      ],
    })
  if (path === '/api/shortcuts/prepare') {
    const ios = String(init?.body ?? '').includes('"ios":"26"') ? '26' : '27'
    return json({
      setup_url: `https://box.example.com/phone-setup?t=abc&ios=${ios}`,
      qr_data_uri: 'data:image/svg+xml,qr',
      expires_in: 600,
      base_url: 'https://box.example.com',
    })
  }
  return new Response('{}', { status: 404 })
})

vi.mock('../../../../api/client', () => ({
  authFetch: (p: string, i?: RequestInit) => authFetch(p, i),
}))

import { ShortcutsSection } from './ShortcutsSection'

function setUA(ua: string, touch = 0) {
  Object.defineProperty(navigator, 'userAgent', { value: ua, configurable: true })
  Object.defineProperty(navigator, 'maxTouchPoints', { value: touch, configurable: true })
}

const writeText = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
  writeText.mockClear()
})
afterEach(cleanup)

it('on a computer, shows the QR straight away instead of downloading anything', async () => {
  setUA('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15')
  const open = vi.spyOn(window, 'open').mockReturnValue(null)
  render(<ShortcutsSection toast={vi.fn()} />)
  // The QR is minted after a 400ms debounce, so allow for a slow machine.
  expect(
    await screen.findByAltText('QR code that opens phone setup', {}, { timeout: 5000 }),
  ).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Show QR code' })).toBeNull()
  expect(open).not.toHaveBeenCalled()
})

it('shows no QR for this computer’s own address — a phone could never open it', async () => {
  setUA('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)')
  render(<ShortcutsSection toast={vi.fn()} />)
  const input = (await screen.findByLabelText('App address')) as HTMLInputElement
  fireEvent.change(input, { target: { value: 'http://localhost:8080' } })
  expect(await screen.findByText('Add your app address')).toBeTruthy()
  expect(screen.queryByAltText('QR code that opens phone setup')).toBeNull()
})

it('on an iPhone, the tab IS the steps — no QR, no sub-page, Install downloads in place', async () => {
  setUA('Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) Version/27.0 Mobile Safari/604.1')
  const open = vi.spyOn(window, 'open').mockReturnValue(null)
  const assign = vi.fn()
  vi.stubGlobal('location', { ...window.location, assign })
  render(<ShortcutsSection toast={vi.fn()} />)

  expect(await screen.findByText('Setup code')).toBeTruthy()
  expect(screen.queryByAltText('QR code that opens phone setup')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(SETUP_CODE))
  expect(document.body.textContent).not.toContain('tok_secret_value')

  expect(screen.getByText('Finance notifications')).toBeTruthy()
  expect(screen.getAllByRole('button', { name: 'Install' })).toHaveLength(2)
  // The link is prefetched, so the tap opens it synchronously (iOS blocks a later open).
  await waitFor(() =>
    expect(authFetch.mock.calls.some(([p]) => String(p).startsWith('/api/shortcuts/setup?'))).toBe(
      true,
    ),
  )
  await new Promise((r) => setTimeout(r, 50))
  fireEvent.click(screen.getAllByRole('button', { name: 'Install' })[0]!)
  await waitFor(() =>
    expect(assign).toHaveBeenCalledWith('/api/shortcuts/file?t=abc&slug=log-apple-pay'),
  )
  expect(open).not.toHaveBeenCalled()
  vi.unstubAllGlobals()
})

it('switches tabs with the arrow keys', async () => {
  setUA('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)')
  render(<ShortcutsSection toast={vi.fn()} />)
  const iphone = await screen.findByRole('tab', { name: 'iOS 27+' })
  fireEvent.keyDown(iphone, { key: 'ArrowRight' })
  expect(screen.getByRole('tab', { name: 'iOS 26 or earlier' }).getAttribute('aria-selected')).toBe(
    'true',
  )
})

it('on Android, starts on the Android recipe: token masked, Reveal shows it, Copy request has it', async () => {
  setUA('Mozilla/5.0 (Linux; Android 15; Pixel 9)')
  render(<ShortcutsSection toast={vi.fn()} />)
  const code = await screen.findByText(/POST https:\/\/box\.example\.com\/api\/capture/)
  expect(code.textContent).not.toContain('tok_secret_value')
  expect(code.closest('[data-ctx-secret]')).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Reveal token' }))
  await waitFor(() => expect(code.textContent).toContain('Bearer tok_secret_value'))
  fireEvent.click(screen.getByRole('button', { name: 'Hide token' }))
  expect(code.textContent).not.toContain('tok_secret_value')

  fireEvent.click(screen.getByRole('button', { name: 'Copy request' }))
  await waitFor(() =>
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('Bearer tok_secret_value')),
  )
})

it('shows the setup code masked until Reveal, and copies it', async () => {
  setUA('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)')
  render(<ShortcutsSection toast={vi.fn()} />)
  const field = () => screen.getByLabelText('Setup code')
  await waitFor(() =>
    expect((field().textContent ?? '').startsWith('https://box.example.com|•')).toBe(true),
  )
  // The region is excluded from the Lucius screen snapshot.
  expect(field().closest('[data-ctx-secret]')).toBeTruthy()

  fireEvent.click(screen.getByRole('button', { name: 'Reveal' }))
  await waitFor(() => expect(field().textContent).toBe(SETUP_CODE))
  fireEvent.click(screen.getByRole('button', { name: 'Hide' }))
  expect(field().textContent).not.toContain('tok_secret_value')

  // Already loaded, so the write happens inside the tap itself — iOS refuses it after a wait.
  writeText.mockClear()
  fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
  expect(writeText).toHaveBeenCalledWith(SETUP_CODE)
  expect(await screen.findByRole('button', { name: 'Copied' })).toBeTruthy()
  expect(document.body.textContent).not.toContain('tok_secret_value')
})

it('warns when the address is this computer', async () => {
  setUA('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)')
  render(<ShortcutsSection toast={vi.fn()} />)
  const input = (await screen.findByLabelText('App address')) as HTMLInputElement
  fireEvent.change(input, { target: { value: 'http://localhost:8080' } })
  expect(screen.getByText(/this computer's own address/)).toBeTruthy()
})

it('on an iOS 26 iPhone, opens on the iOS 26 version: its file and the automation steps', async () => {
  setUA('Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) Version/26.1 Mobile Safari/604.1')
  render(<ShortcutsSection toast={vi.fn()} />)
  const tab = await screen.findByRole('tab', { name: 'iOS 26 or earlier' })
  expect(tab.getAttribute('aria-selected')).toBe('true')
  expect(screen.getByText('Run Immediately')).toBeTruthy()
  expect(screen.getAllByRole('button', { name: 'Install' })).toHaveLength(1)
  expect(screen.queryByText('Finance notifications')).toBeNull()
})

it('on a computer, the QR follows the iOS switch', async () => {
  setUA('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)')
  render(<ShortcutsSection toast={vi.fn()} />)
  await screen.findByAltText('QR code that opens phone setup', {}, { timeout: 5000 })
  fireEvent.click(screen.getByRole('tab', { name: 'iOS 26 or earlier' }))
  await waitFor(
    () =>
      expect(
        authFetch.mock.calls.some(
          ([p, i]) => p === '/api/shortcuts/prepare' && String(i?.body).includes('"ios":"26"'),
        ),
      ).toBe(true),
    { timeout: 5000 },
  )
  expect(screen.getByText('Run Immediately')).toBeTruthy()
  expect(screen.queryByText('Finance notifications')).toBeNull()
})

it('on an iPhone, coming back to the app re-fetches the install links', async () => {
  setUA('Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) Version/27.0 Mobile Safari/604.1')
  render(<ShortcutsSection toast={vi.fn()} />)
  const setups = () =>
    authFetch.mock.calls.filter(([p]) => String(p).startsWith('/api/shortcuts/setup?')).length
  await waitFor(() => expect(setups()).toBeGreaterThan(0))
  await new Promise((r) => setTimeout(r, 50))
  const before = setups()
  document.dispatchEvent(new Event('visibilitychange'))
  await waitFor(() => expect(setups()).toBe(before + 1))
})

it('on a computer, lists both shortcuts with their steps — Install is the phone’s job', async () => {
  setUA('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)')
  render(<ShortcutsSection toast={vi.fn()} />)
  expect(await screen.findByText('Apple Pay taps')).toBeTruthy()
  expect(screen.getByText('Finance notifications')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'Install' })).toBeNull()
})
