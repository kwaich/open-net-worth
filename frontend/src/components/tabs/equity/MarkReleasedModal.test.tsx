// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { authFetch } from '../../../api/client'
import type { PendingReleaseItem } from '../../../types/equity'
import { MarkReleasedModal } from './MarkReleasedModal'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

const item: PendingReleaseItem = {
  vest_event_id: 'vest-1',
  grant_id: 'grant-1',
  tranche_id: 'tranche-1',
  symbol: 'KSTL',
  instrument_id: 'instrument-1',
  vest_date: '2026-09-15',
  units: 488.75,
  fmv_per_unit: 20.36,
  fmv_currency: 'EUR',
  expected_release_date: null,
  overdue: false,
  indicative_value_base: null,
  base_currency: 'SGD',
  release_mode: 'move_pending',
}

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.resetAllMocks()
})

it('defaults to the local delivery day at just after midnight', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(2026, 8, 26, 1, 9))
  render(<MarkReleasedModal item={item} onClose={vi.fn()} onDone={vi.fn()} />)
  expect((screen.getByLabelText('Release date') as HTMLInputElement).value).toBe('2026-09-26')
})

it('describes a modern release without claiming cash income was recorded at vest', async () => {
  vi.mocked(authFetch).mockResolvedValue({
    ok: true,
    json: async () => ({ vest_event_id: 'vest-1', release_mode: 'move_pending' }),
  } as Response)
  render(<MarkReleasedModal item={item} onClose={vi.fn()} onDone={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'Confirm release' }))
  expect(await screen.findByText(/No cash transaction was written/)).toBeTruthy()
  expect(screen.queryByText(/income was already recorded at vest/)).toBeNull()
})
