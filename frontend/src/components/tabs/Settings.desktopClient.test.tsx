// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { DesktopBridge, DesktopClientBridge } from '../../../../desktop/src/contracts'
import { Settings } from './Settings'

vi.mock('../../api/client', () => ({
  authFetch: vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) })),
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('./settings/sections/ProfileSection', () => ({
  ProfileSection: () => <div>Profile form</div>,
}))
vi.mock('./settings/sections/DesktopSection', () => ({
  DesktopSection: () => <div>Desktop controls</div>,
}))

function desktopItem() {
  return Array.from(document.body.querySelectorAll<HTMLElement>('.rd-set-navitem')).find(
    (item) => item.textContent?.trim() === 'Desktop',
  )
}

beforeEach(() => {
  delete window.onwDesktop
  window.onwDesktopClient = {
    version: 1,
    getConnection: async () => ({
      mode: 'server',
      serverUrl: 'https://book.example.test/',
      bookFolder: null,
    }),
    changeConnection: async () => {},
    openBrowser: async () => {},
    onCommand: () => () => {},
  } satisfies DesktopClientBridge
})

afterEach(() => {
  cleanup()
  delete window.onwDesktop
  delete window.onwDesktopClient
})

it('shows Desktop navigation and accepts its deep link for a client-only server connection', () => {
  render(<Settings subTabRequest="desktop" />)
  expect(desktopItem()?.getAttribute('aria-current')).toBe('page')
})

it('accepts a later Desktop deep link while server Settings is already open', () => {
  const { rerender } = render(<Settings />)
  expect(desktopItem()?.getAttribute('aria-current')).toBeNull()
  rerender(<Settings subTabRequest="desktop" />)
  expect(desktopItem()?.getAttribute('aria-current')).toBe('page')
})

it('keeps Desktop navigation and deep links unavailable in an ordinary browser', () => {
  delete window.onwDesktopClient
  render(<Settings subTabRequest="desktop" />)
  expect(desktopItem()).toBeUndefined()
  expect(document.body.querySelector('.rd-set-navitem[aria-current="page"]')?.textContent).toBe(
    'Profile',
  )
})

it('preserves Desktop navigation for the full local bridge', () => {
  delete window.onwDesktopClient
  window.onwDesktop = { version: 1 } as DesktopBridge
  render(<Settings subTabRequest="desktop" />)
  expect(desktopItem()?.getAttribute('aria-current')).toBe('page')
})
