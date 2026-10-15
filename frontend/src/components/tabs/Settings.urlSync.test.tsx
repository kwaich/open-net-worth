// Regression test: switching a Settings section mirrors into the URL (?sub=) — part of the
// same fix as Plan's ?sub= (owner: "fix it for all tabs, not just plan tab"). Also pins the fix
// for Overview's "Import a statement" deep-link, which used to write a localStorage key
// directly before navigating (the exact anti-pattern that caused the Plan/Outlook bug) and now
// passes ?sub=data instead. The URL is the ONLY place the section lives: nothing is stored
// across a close (see Settings.reopen.test.tsx).
//
// Updated for the 2026-08-05 overlay redesign: the section rail is a list of buttons carrying
// aria-current, not a PageTabs tablist. The three behaviours pinned here are unchanged.
//
// The last case pins the fix for a router update-depth loop: with the deep-link applied by an
// EFFECT rather than seeded into the initial state, one mount wrote ?sub= twice — once for the
// remembered section, once to correct it — and TanStack re-ran load() until it gave up.
// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { Settings } from './Settings'

// ok:false (not ok:true+{}) — several Settings sections (e.g. AI spend) treat a shape-less
// 200 as real empty data and crash reading nested fields; a non-ok response is their designed
// error/empty path, which every such section already guards against.
vi.mock('../../api/client', () => ({
  authFetch: vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({}) }),
}))
const mockNavigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigate,
}))

// The overlay renders through the design system Modal's portal, so the rail is under
// document.body, not the render container. Lucide icons contribute no text, so the visible
// label is the button's complete text content.
const railItem = (label: string): HTMLElement => {
  const hit = Array.from(document.body.querySelectorAll<HTMLElement>('.rd-set-navitem')).find(
    (el) => el.textContent?.trim() === label,
  )
  if (!hit) throw new Error(`no rail item labelled ${label}`)
  return hit
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('Settings — sub-section URL sync', () => {
  it('mirrors the default section into the URL on mount', () => {
    render(<Settings />)
    expect(mockNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ to: '/settings', replace: true }),
    )
  })

  it('applies an inbound ?sub=data deep-link request (the Overview "Import a statement" flow)', () => {
    render(<Settings subTabRequest="data" />)
    expect(railItem('Data').getAttribute('aria-current')).toBe('page')
  })

  it('deep-links ?sub=intel to the Intelligence section, and search finds it by "opinion"', () => {
    render(<Settings subTabRequest="intel" />)
    expect(railItem('Intelligence').getAttribute('aria-current')).toBe('page')
    fireEvent.change(document.body.querySelector('[aria-label="Search settings"]')!, {
      target: { value: 'opinion' },
    })
    const labels = Array.from(document.body.querySelectorAll<HTMLElement>('.rd-set-navitem')).map(
      (item) => item.textContent?.trim(),
    )
    expect(labels).toEqual(['Intelligence'])
  })

  it('exposes Cost & Performance as the final deep-linkable section after Developer', () => {
    render(<Settings subTabRequest="aiUsage" />)
    expect(railItem('Cost & Performance').getAttribute('aria-current')).toBe('page')
    const labels = Array.from(document.body.querySelectorAll<HTMLElement>('.rd-set-navitem')).map(
      (item) => item.textContent?.trim(),
    )
    expect(labels.slice(-2)).toEqual(['Developer', 'Cost & Performance'])
  })

  it('mirrors a user-driven section switch into the URL', () => {
    render(<Settings />)
    fireEvent.click(railItem('Appearance'))
    const lastCall = mockNavigate.mock.calls[mockNavigate.mock.calls.length - 1]![0]
    expect(lastCall.to).toBe('/settings')
    expect(lastCall.search({})).toEqual({ sub: 'appearance' })
  })

  it('does not re-write a ?sub= the URL already carries', () => {
    render(<Settings subTabRequest="data" />)
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('applies a LATER ?sub= change that arrives as a prop (the router round trip)', () => {
    // The case the update-depth loop lived in: Settings is already open on one section when
    // Overview deep-links to another. Mounting with the value cannot exercise it — the prop
    // has to CHANGE, the way it does when the URL flows back through RouterShell.
    const { rerender } = render(<Settings subTabRequest="permissions" />)
    expect(railItem('Permissions').getAttribute('aria-current')).toBe('page')

    rerender(<Settings subTabRequest="data" />)
    expect(railItem('Data').getAttribute('aria-current')).toBe('page')
    expect(railItem('Permissions').getAttribute('aria-current')).toBeNull()
  })

  it('lets the user switch by hand after a deep-link, and does not snap back', () => {
    const { rerender } = render(<Settings subTabRequest="data" />)
    fireEvent.click(railItem('Security'))
    expect(railItem('Security').getAttribute('aria-current')).toBe('page')

    // The router echoes the click back as the same prop it already had — the guard must not
    // re-apply it and drag the user back to Data.
    rerender(<Settings subTabRequest="data" />)
    expect(railItem('Security').getAttribute('aria-current')).toBe('page')
  })
})
