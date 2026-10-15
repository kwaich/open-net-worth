// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import * as client from '../../../../api/client'
import { clearQueryCache } from '../../../../state/queryCache'
import { CurrencySection } from './CurrencySection'

function respond(profile: Record<string, unknown>) {
  return vi.spyOn(client, 'authFetch').mockImplementation(async (url, init) => {
    if (String(url).endsWith('/timezone') && init?.method === 'PUT') {
      const { timezone } = JSON.parse(String(init.body))
      return {
        ok: true,
        json: async () => ({ timezone, timezone_source: 'user' }),
      } as Response
    }
    return { ok: true, json: async () => profile } as Response
  })
}

beforeEach(() => {
  clearQueryCache()
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({
    timeZone: 'Asia/Singapore',
  } as Intl.ResolvedDateTimeFormatOptions)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('Settings time zone', () => {
  it("shows the stored zone and saves a pick as the owner's choice", async () => {
    const spy = respond({
      base_currency: 'SGD',
      timezone: 'Asia/Singapore',
      timezone_source: 'auto',
    })
    const toast = vi.fn()
    render(<CurrencySection toast={toast} />)
    await screen.findByText(/Detected from this browser/)
    const select = screen.getByDisplayValue('Asia/Singapore') as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'Europe/Paris' } })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('Time zone updated'))
    expect(spy).toHaveBeenCalledWith(
      '/api/profile/timezone',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ timezone: 'Europe/Paris' }),
      }),
    )
    await screen.findByText('Set by you')
  })

  it('offers to switch to the browser zone only when it differs from the stored one', async () => {
    respond({ base_currency: 'SGD', timezone: 'Europe/Paris', timezone_source: 'user' })
    render(<CurrencySection toast={vi.fn()} />)
    await screen.findByText(/Use this browser's \(Asia\/Singapore\)/)
  })
})
