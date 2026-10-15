// @vitest-environment jsdom

import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Grant, Tranche } from '../../../types/equity'
import { MarkVestModal } from './MarkVestModal'

vi.mock('../../../api/client', () => ({
  authFetch: vi.fn(() => new Promise(() => {})),
}))

const tranche: Tranche = {
  id: 'tranche-1',
  vest_date: '2026-09-15',
  units: 488.75,
  kind: 'time',
  condition_note: null,
  vested: false,
  vest_event_id: null,
  status: 'upcoming',
}

const grant: Grant = {
  id: 'grant-1',
  type: 'rsu',
  instrument_id: 'instrument-1',
  symbol: 'KSTL',
  grant_date: '2024-09-15',
  total_units: 488.75,
  vested_units: 0,
  unvested_units: 488.75,
  next_vest_date: tranche.vest_date,
  currency: 'EUR',
  grant_price_per_unit: null,
  tax_event: 'at_vest',
  status: 'active',
  release_required: false,
  pending_release_units: 0,
  locked_position_id: null,
  pending_position_id: null,
  account_id: null,
  tranches: [tranche],
  note: '',
  created_at: '2024-09-15T00:00:00Z',
  updated_at: '2024-09-15T00:00:00Z',
}

function mockMobile() {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

afterEach(cleanup)

describe('MarkVestModal mobile dismissal', () => {
  it('keeps backdrop dismissal off but closes from a committed pull gesture', async () => {
    const restore = mockMobile()
    const onClose = vi.fn()

    try {
      render(<MarkVestModal grant={grant} tranche={tranche} onClose={onClose} onDone={vi.fn()} />)

      const card = document.querySelector('.ui-modal-card') as HTMLElement
      const handle = document.querySelector('.ui-modal-handle-wrap') as HTMLElement
      expect(card.dataset.onwDismissable).toBe('false')
      expect(handle).toBeTruthy()

      Object.defineProperty(card, 'offsetHeight', { value: 400, configurable: true })
      fireEvent.pointerDown(handle, { clientY: 200, pointerId: 1 })
      await new Promise((resolve) => setTimeout(resolve, 150))
      fireEvent.pointerMove(handle, { clientY: 400, pointerId: 1 })
      fireEvent.pointerUp(handle, { clientY: 400, pointerId: 1 })

      expect(onClose).toHaveBeenCalledTimes(1)
    } finally {
      restore()
    }
  })
})
