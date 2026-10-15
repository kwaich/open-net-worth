// @vitest-environment jsdom
//
// Regression: the "Find your bank" search row rendered three controls at three different
// vertical positions. The row was a `Cluster align="end"` — flex, bottom-aligned — and the
// country field carried its own help text, so that field's box was taller than its neighbour's
// by exactly one line. Bottom-aligning the boxes therefore lifted the country LABEL and INPUT
// a line above the bank-name pair, left the (padding-sized) Search button level with neither,
// and parked the help text alongside the bank-name field it was crowding.
//
// The fix is structural, so the assertions are structural: the help is a child of the ROW, the
// three controls are siblings in it, and they speak the redesign's fixed-height vocabulary
// (spec §9) rather than the padding-sized `ui/` one. The grid does the rest, in one place.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { InstitutionPickerModal } from './InstitutionPickerModal'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function open() {
  return render(
    <div className="cn-page">
      <InstitutionPickerModal open onClose={() => {}} onRouteToProvider={() => {}} />
    </div>,
  )
}

describe('the bank picker search row', () => {
  it('holds country, bank name and Search as siblings of one row', () => {
    const { container } = open()
    const row = container.querySelector('.cn-linkrow')!
    expect(row).toBeTruthy()

    const kids = Array.from(row.children)
    expect(kids.map((k) => k.className)).toEqual([
      'rd-field',
      'rd-field',
      'rd-help',
      'rd-btn secondary',
    ])
    // Every control is placed by the row itself — none is nested inside another's field.
    expect(row.querySelectorAll(':scope > .rd-field > .rd-input')).toHaveLength(2)
  })

  it('keeps the country help out of the country field, so the two fields match in height', () => {
    const { container } = open()
    const help = container.querySelector('#link-country-help')!
    expect(help.parentElement?.className).toBe('cn-linkrow')
    expect(container.querySelector('.rd-field .rd-help')).toBeNull()
    // Still ANNOUNCED with the field it describes, which is what the wrapper used to buy.
    expect(container.querySelector('#link-country')?.getAttribute('aria-describedby')).toBe(
      'link-country-help',
    )
  })

  it('uses fixed-height controls (spec §9), not the padding-sized ui/ ones', () => {
    const { container } = open()
    expect(container.querySelectorAll('.cn-linkrow .ui-input, .cn-linkrow .ui-btn')).toHaveLength(0)
    expect(container.querySelector('.cn-linkrow .rd-btn')?.textContent).toBe('Search')
  })

  it('still labels both boxes and searches on the country typed into one', async () => {
    authFetch.mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ institutions: [], providers: [], manual_import: true }),
    })
    open()
    fireEvent.change(screen.getByLabelText(/^Country/), { target: { value: 'de' } })
    expect(screen.getByLabelText(/^Bank name/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    await waitFor(() =>
      expect(authFetch).toHaveBeenCalledWith('/api/account-linking/institutions?country=DE'),
    )
  })

  it('leaves Close alone in the footer, so the phone rule never stretches it', () => {
    const { container } = open()
    const foot = container.querySelector('.rd-modal-foot')!
    expect(Array.from(foot.children).map((c) => c.textContent)).toEqual(['Close'])
  })
})
