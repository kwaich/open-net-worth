// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../state/queryCache'
import { useInbox, type InboxItem } from './useInbox'

const authFetch = vi.fn()
vi.mock('../api/client', () => ({ authFetch: (...args: unknown[]) => authFetch(...args) }))
vi.mock('../api/agents', () => ({
  reviewFinding: vi.fn(),
  applyFinding: vi.fn(),
}))

const item = (id: string): InboxItem => ({
  id,
  kind: 'signal',
  title: id,
  summary: id,
  status: 'new',
  created_at: '2026-07-23T00:00:00Z',
  source: 'test',
  proposed_change: null,
  ref: { id, tab: 'inbox' },
  read: false,
})

const response = (items: InboxItem[]) =>
  Promise.resolve({ ok: true, json: () => Promise.resolve({ items }) } as Response)

describe('useInbox remount cache', () => {
  beforeEach(() => {
    clearQueryCache()
    authFetch.mockReset()
  })

  it('renders last-good items immediately after unmount while revalidating', async () => {
    authFetch.mockImplementationOnce(() => response([item('first')]))
    const first = renderHook(() => useInbox(false))
    await waitFor(() => expect(first.result.current.items?.[0]?.id).toBe('first'))
    first.unmount()

    authFetch.mockImplementationOnce(() => new Promise<Response>(() => {}))
    const second = renderHook(() => useInbox(false))
    expect(second.result.current.items?.[0]?.id).toBe('first')
    expect(second.result.current.loading).toBe(false)
  })

  it('keeps a dismissed item removed across remount before revalidation completes', async () => {
    authFetch.mockImplementationOnce(() => response([item('gone'), item('kept')]))
    const all = renderHook(() => useInbox(true))
    await waitFor(() => expect(all.result.current.items).toHaveLength(2))
    all.unmount()

    authFetch.mockImplementationOnce(() => response([item('gone'), item('kept')]))
    const unread = renderHook(() => useInbox(false))
    await waitFor(() => expect(unread.result.current.items).toHaveLength(2))
    authFetch.mockResolvedValueOnce({ ok: true } as Response)
    await act(async () => unread.result.current.dismiss(item('gone')))
    expect(unread.result.current.items?.map((it) => it.id)).toEqual(['kept'])
    unread.unmount()

    authFetch.mockImplementationOnce(() => new Promise<Response>(() => {}))
    const second = renderHook(() => useInbox(true))
    expect(second.result.current.items?.map((it) => it.id)).toEqual(['kept'])
  })
})
