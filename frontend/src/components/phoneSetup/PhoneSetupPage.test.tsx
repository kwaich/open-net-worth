// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PhoneSetupPage } from './PhoneSetupPage'
import { resetPhoneSetupForTests } from './setupData'

const DATA = {
  setup_code: 'https://box.example.com|tok_example',
  expires_in: 600,
  shortcuts: [
    {
      slug: 'log-apple-pay',
      name: 'Log Apple Pay to Open Net Worth',
      summary: 'Sends an Apple Pay tap.',
      asks: 'setup code',
      install_url: '/api/shortcuts/file?t=abc&slug=log-apple-pay',
    },
    {
      slug: 'log-apple-pay-ios26',
      name: 'Log Apple Pay to Open Net Worth',
      summary: 'iOS 26 and earlier.',
      asks: 'setup code',
      install_url: '/api/shortcuts/file?t=abc&slug=log-apple-pay-ios26',
    },
    {
      slug: 'log-notifications',
      name: 'Log Finance Notifications to Open Net Worth',
      summary: 'iOS 27+.',
      asks: 'setup code',
      install_url: '/api/shortcuts/file?t=abc&slug=log-notifications',
    },
  ],
}

const writeText = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  resetPhoneSetupForTests()
  window.history.replaceState(null, '', '/phone-setup?t=abc')
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

it('loads the ticket and shows the setup code and both signed installs', async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(DATA)))
  vi.stubGlobal('fetch', fetchMock)
  render(<PhoneSetupPage />)

  expect(await screen.findByLabelText('Setup code')).toBeTruthy()
  expect(fetchMock).toHaveBeenCalledWith('/api/shortcuts/setup?t=abc', {
    credentials: 'same-origin',
  })
  expect(screen.getByText('Apple Pay taps')).toBeTruthy()
  // iOS 27: the optional notifications shortcut, with the one-trigger-per-card warning.
  expect(screen.getByText('Finance notifications')).toBeTruthy()
  expect(screen.queryByText(/logs twice/)).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(DATA.setup_code))
  expect(await screen.findByRole('button', { name: 'Copied' })).toBeTruthy()
})

it('says so when the shortcut is unsigned instead of offering a button iOS would refuse', async () => {
  const unsigned = { ...DATA, shortcuts: [{ ...DATA.shortcuts[0]!, install_url: null }] }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(unsigned))))
  render(<PhoneSetupPage />)
  expect(await screen.findByText('Not signed')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /^Install/ })).toBeNull()
})

it('explains a dead link instead of showing an error body', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', { status: 404 })))
  render(<PhoneSetupPage />)
  expect(await screen.findByText(/already opened on another device/)).toBeTruthy()
})

it('opens on the version the QR was made for, and switches its install and guide', async () => {
  window.history.replaceState(null, '', '/phone-setup?t=abc&ios=26')
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(DATA))))
  const open = vi.fn()
  vi.stubGlobal('location', { ...window.location, assign: open, search: '?t=abc&ios=26' })
  render(<PhoneSetupPage />)
  expect(await screen.findByText('Run Immediately')).toBeTruthy()
  expect(screen.queryByText('Finance notifications')).toBeNull()

  fireEvent.click(screen.getByRole('tab', { name: 'iOS 27+' }))
  expect(await screen.findByText('Apple Pay taps')).toBeTruthy()
  expect(screen.getByText('Finance notifications')).toBeTruthy()
})
