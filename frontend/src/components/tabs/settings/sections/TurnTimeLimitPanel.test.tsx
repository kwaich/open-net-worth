// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

vi.mock('../../../../api/client', () => ({ authFetch: vi.fn() }))

import { authFetch } from '../../../../api/client'
import { TURN_TIME_LIMIT_KEY, turnTimeLimitLabel } from '../../../../api/turnTimeLimit'
import { deleteCachedQueryData } from '../../../../state/queryCache'
import { TurnTimeLimitPanel } from './TurnTimeLimitPanel'

const mockAuthFetch = vi.mocked(authFetch)
let stored = 1200

beforeEach(() => {
  stored = 1200
  deleteCachedQueryData(TURN_TIME_LIMIT_KEY)
  mockAuthFetch.mockImplementation(async (_url, init) => {
    if (init?.method === 'PUT') stored = JSON.parse(String(init.body)).seconds
    const body = { seconds: stored, default_seconds: 1200, choices: [1200, 1800, 3600, 7200, 0] }
    return new Response(JSON.stringify(body), { status: 200 })
  })
})

afterEach(() => cleanup())

it('labels the choices, default and no limit included', () => {
  expect(turnTimeLimitLabel(1200, 1200)).toBe('20 minutes (default)')
  expect(turnTimeLimitLabel(3600)).toBe('1 hour')
  expect(turnTimeLimitLabel(7200)).toBe('2 hours')
  expect(turnTimeLimitLabel(0)).toBe('No limit')
})

it('saves the picked limit, up to no limit', async () => {
  const toast = vi.fn()
  render(<TurnTimeLimitPanel toast={toast} />)
  const select = await screen.findByRole('combobox')
  await waitFor(() => expect((select as HTMLSelectElement).value).toBe('1200'))

  fireEvent.change(select, { target: { value: '0' } })

  await waitFor(() => expect(toast).toHaveBeenCalledWith('Turn time limit: No limit'))
  const put = mockAuthFetch.mock.calls.find(([, init]) => init?.method === 'PUT')
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({ seconds: 0 })
})

it('shows the pick at once, before the save answers', async () => {
  let answer: (r: Response) => void = () => {}
  mockAuthFetch.mockImplementation((_url, init) => {
    const body = { seconds: stored, default_seconds: 1200, choices: [1200, 1800, 3600, 7200, 0] }
    if (init?.method !== 'PUT') return Promise.resolve(new Response(JSON.stringify(body)))
    stored = JSON.parse(String(init.body)).seconds
    return new Promise<Response>((resolve) => (answer = resolve))
  })
  render(<TurnTimeLimitPanel toast={vi.fn()} />)
  const select = (await screen.findByRole('combobox')) as HTMLSelectElement
  await waitFor(() => expect(select.value).toBe('1200'))

  fireEvent.change(select, { target: { value: '3600' } })
  expect(select.value).toBe('3600') // no flash back to the old value while saving

  answer(
    new Response(
      JSON.stringify({ seconds: 3600, default_seconds: 1200, choices: [1200, 3600, 0] }),
    ),
  )
  await waitFor(() => expect(select.value).toBe('3600'))
})

it('puts the old value back only when the save fails', async () => {
  mockAuthFetch.mockImplementation(async (_url, init) => {
    if (init?.method === 'PUT') return new Response('nope', { status: 500 })
    const body = { seconds: 1800, default_seconds: 1200, choices: [1200, 1800, 3600, 7200, 0] }
    return new Response(JSON.stringify(body), { status: 200 })
  })
  const toast = vi.fn()
  render(<TurnTimeLimitPanel toast={toast} />)
  const select = (await screen.findByRole('combobox')) as HTMLSelectElement
  await waitFor(() => expect(select.value).toBe('1800'))

  fireEvent.change(select, { target: { value: '3600' } })
  await waitFor(() => expect(toast).toHaveBeenCalledWith('Could not save — still 30 minutes'))
  expect(select.value).toBe('1800')
})
