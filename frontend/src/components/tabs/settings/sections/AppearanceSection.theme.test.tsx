// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as client from '../../../../api/client'
import { AppearanceSection } from './AppearanceSection'

beforeEach(() => {
  localStorage.clear()
  localStorage.setItem('onw-theme', 'system')
  vi.spyOn(client, 'authFetch').mockResolvedValue({
    ok: true,
    json: async () => ({ app_name: 'Open Net Worth', accent_color: '', logo_url: '' }),
  } as Response)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Appearance theme selector', () => {
  it('offers System, Dark, Warm, and Light as distinct device choices', () => {
    render(<AppearanceSection toast={vi.fn()} />)

    const options = screen.getAllByRole('option') as HTMLOptionElement[]
    expect(options.map((option) => [option.value, option.textContent])).toEqual([
      ['system', 'System'],
      ['dark', 'Dark'],
      ['warm', 'Warm'],
      ['light', 'Light'],
    ])
    expect(screen.queryByText(/Following your system|currently dark/i)).toBeNull()
  })
})
