// Regression (owner, 2026-10-03): the phone Settings menu listed "Desktop" in an ordinary browser,
// and tapping it opened a blank page — the drill-down menu skipped the availability filter the
// wide rail applies.
// @vitest-environment jsdom

import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import type { DesktopBridge } from '../../../../desktop/src/contracts'
import { SECTIONS, Settings } from './Settings'

vi.mock('../../api/client', () => ({
  authFetch: vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }),
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../../hooks/useIsMobile', () => ({ useIsMobile: () => true }))

const menuLabels = () =>
  Array.from(document.body.querySelectorAll<HTMLElement>('.rd-set-menurow')).map((el) =>
    el.textContent?.trim(),
  )

afterEach(() => {
  cleanup()
  delete window.onwDesktop
})

it('phone: an ordinary browser does not list the Desktop section', () => {
  render(<Settings />)
  expect(menuLabels()).toContain('Appearance')
  expect(menuLabels()).not.toContain('Desktop')
})

it('phone: the desktop app still lists it', () => {
  window.onwDesktop = { version: 1 } as DesktopBridge
  render(<Settings />)
  expect(menuLabels()).toContain('Desktop')
})

it('phone: every section is reachable from a menu group', () => {
  window.onwDesktop = { version: 1 } as DesktopBridge
  render(<Settings />)
  const labels = menuLabels()
  // Terminal is gated on a server probe (failed here), Profile is the account card on top.
  const expected = SECTIONS.filter((s) => s.key !== 'terminal' && s.key !== 'profile')
  for (const s of expected) expect(labels).toContain(s.label)
  expect(labels).toContain('Phone shortcuts')
  expect(labels).toContain('MCP')
  expect(labels).toContain('Help')
})
