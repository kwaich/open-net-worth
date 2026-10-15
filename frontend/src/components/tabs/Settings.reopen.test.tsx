// Regression test (owner, 2026-09-05): "On mobile, when I open a sub tab on settings and then
// I dismiss the settings, when I open again, it keeps the sub tab open. Let's not cache or store
// that." Two things combined to do it: the section was remembered in localStorage across
// dismiss, and the phone sheet drilled in on ANY ?sub= — including the backdrop tab's own
// (/investments?sub=activity leaks into /settings?sub=activity via goTo's search merge), so a
// fresh open landed inside the remembered section. Settings now opens at its top level unless
// the URL names one of ITS sections.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { Settings } from './Settings'

vi.mock('../../api/client', () => ({
  authFetch: vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}))
let mobile = false
vi.mock('../../hooks/useIsMobile', () => ({
  useIsMobile: () => mobile,
}))

const sheetTitle = () => document.body.querySelector('.rd-set-sheettitle')?.textContent
const menuRow = (label: string): HTMLElement => {
  const hit = Array.from(document.body.querySelectorAll<HTMLElement>('.rd-set-menurow')).find(
    (el) => el.textContent?.trim() === label,
  )
  if (!hit) throw new Error(`no menu row labelled ${label}`)
  return hit
}
const railCurrent = () =>
  document.body.querySelector<HTMLElement>('.rd-set-navitem[aria-current="page"]')?.textContent

afterEach(() => {
  cleanup()
  mobile = false
  localStorage.clear()
})

describe('Settings — reopening starts at the top level', () => {
  it('phone: a section opened before dismiss is NOT restored on the next open', () => {
    mobile = true
    render(<Settings />)
    fireEvent.click(menuRow('Appearance'))
    expect(sheetTitle()).toBe('Appearance')
    cleanup()

    // The owner's exact path: reopened from the Investments tab, whose own ?sub=activity rides
    // along into /settings. It used to land inside the remembered Appearance section.
    render(<Settings subTabRequest="activity" />)
    expect(sheetTitle()).toBe('Settings')
    expect(document.body.querySelector('.rd-set-menu')).not.toBeNull()
  })

  it('phone: the backdrop tab\'s own ?sub= (e.g. investments "activity") does not drill in', () => {
    mobile = true
    render(<Settings subTabRequest="activity" />)
    expect(sheetTitle()).toBe('Settings')
    expect(document.body.querySelector('.rd-set-menu')).not.toBeNull()
  })

  it('phone: a ?sub= naming a Settings section still deep-links straight into it', () => {
    mobile = true
    render(<Settings subTabRequest="data" />)
    expect(sheetTitle()).toBe('Data')
    expect(document.body.querySelector('.rd-set-menu')).toBeNull()
  })

  it('desktop: a switched section is not remembered across unmount, and nothing is stored', () => {
    render(<Settings />)
    const appearance = Array.from(
      document.body.querySelectorAll<HTMLElement>('.rd-set-navitem'),
    ).find((el) => el.textContent?.trim() === 'Appearance')!
    fireEvent.click(appearance)
    expect(railCurrent()).toBe('Appearance')
    expect(localStorage.length).toBe(0)
    cleanup()

    render(<Settings />)
    expect(railCurrent()).toBe('Profile')
  })
})
