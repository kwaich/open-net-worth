// @vitest-environment jsdom
//
// The browser reports its zone once, only while the book has none. The server decides whether it
// lands (it never overwrites a stored zone); the client's job is to not nag and to mirror the answer.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, waitFor } from '@testing-library/react'
import * as client from '../api/client'
import * as queryCache from '../state/queryCache'
import { useTimezoneCapture } from './useTimezoneCapture'
import type { ProfileData } from './serverState'

function Probe({ profile }: { profile: ProfileData | undefined }) {
  useTimezoneCapture(profile)
  return null
}

beforeEach(() => {
  vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockReturnValue({
    timeZone: 'Asia/Singapore',
  } as Intl.ResolvedDateTimeFormatOptions)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('useTimezoneCapture', () => {
  it('reports the browser zone when the profile has none, then mirrors the answer', async () => {
    const fetchSpy = vi.spyOn(client, 'authFetch').mockResolvedValue({
      ok: true,
      json: async () => ({ timezone: 'Asia/Singapore', timezone_source: 'auto', applied: true }),
    } as Response)
    const setSpy = vi.spyOn(queryCache, 'setCachedQueryData')
    render(<Probe profile={{ base_currency: 'SGD' }} />)
    await waitFor(() => expect(setSpy).toHaveBeenCalled())
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/profile/timezone/detect',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ timezone: 'Asia/Singapore' }),
      }),
    )
  })

  it('stays silent when a zone is already stored, or before the profile loads', () => {
    const fetchSpy = vi.spyOn(client, 'authFetch')
    render(<Probe profile={{ timezone: 'Europe/Paris', timezone_source: 'user' }} />)
    render(<Probe profile={undefined} />)
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('asks only once per session even as the profile re-renders', async () => {
    const fetchSpy = vi.spyOn(client, 'authFetch').mockResolvedValue({ ok: false } as Response)
    const { rerender } = render(<Probe profile={{ base_currency: 'SGD' }} />)
    await waitFor(() => expect(fetchSpy).toHaveBeenCalledTimes(1))
    rerender(<Probe profile={{ base_currency: 'USD' }} />)
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })
})
