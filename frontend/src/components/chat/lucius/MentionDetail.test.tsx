// @vitest-environment jsdom
/**
 * MentionDetail.test.tsx — the chip detail popover.
 *
 * Regression: the ID row must render for an ON-SCREEN entity too. The DOM
 * data-ctx-fields never carries `id` (it is not a descriptor field), so the
 * on-screen byId() path must inject detail.id — consistent with the off-screen
 * fallback / cache / resolve paths. Before the fix the ID was shown off-screen
 * but silently dropped for any entity currently on screen.
 */

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { MentionDetail } from './MentionDetail'

const RECT = {
  left: 100,
  top: 100,
  bottom: 120,
  right: 200,
  width: 100,
  height: 20,
  x: 100,
  y: 100,
  toJSON: () => ({}),
} as DOMRect

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
})

describe('MentionDetail — ID row', () => {
  it('shows the ID as a row for an on-screen transaction (data-ctx-fields omits id)', () => {
    const el = document.createElement('span')
    el.setAttribute('data-entity-id', 'transaction:txn-99')
    el.setAttribute(
      'data-ctx-fields',
      JSON.stringify({ date: '2026-06-16', amount_base: '-1842.10' }),
    )
    document.body.appendChild(el)

    render(
      <MentionDetail
        detail={{ type: 'transaction', id: 'txn-99', label: 'Vantage Brokerage', rect: RECT }}
        onClose={() => {}}
      />,
    )

    const rows = Array.from(document.querySelectorAll('.mx-detail-row'))
    const idRow = rows.find((r) => r.querySelector('.k')?.textContent === 'ID')
    expect(idRow).toBeTruthy()
    expect(idRow?.querySelector('.v')?.textContent).toBe('txn-99')
  })

  it('marks amount rows with .money class while leaving ID and date rows plain', () => {
    const el = document.createElement('span')
    el.setAttribute('data-entity-id', 'transaction:txn-100')
    el.setAttribute(
      'data-ctx-fields',
      JSON.stringify({ date: '2026-06-16', amount: '-184.20 SGD', payee: 'Grab' }),
    )
    document.body.appendChild(el)

    render(
      <MentionDetail
        detail={{
          type: 'transaction',
          id: 'txn-100',
          label: 'Grab',
          note: 'SGD 14,710 • Gemini',
          rect: RECT,
        }}
        onClose={() => {}}
      />,
    )

    const rows = Array.from(document.querySelectorAll('.mx-detail-row'))
    const amtRow = rows.find((r) =>
      r.querySelector('.k')?.textContent?.toLowerCase().includes('amount'),
    )
    const idRow = rows.find((r) => r.querySelector('.k')?.textContent === 'ID')

    if (amtRow) {
      expect(amtRow.querySelector('.v')?.classList.contains('money')).toBe(true)
    }
    expect(idRow?.querySelector('.v')?.classList.contains('money')).toBe(false)

    // Note in header also renders maskable amount
    const typeEl = document.querySelector('.mx-detail-type')
    expect(typeEl?.querySelector('.money')?.textContent).toBe('SGD 14,710')
  })
})
