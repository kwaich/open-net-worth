// @vitest-environment jsdom
//
// useTransactionReceipts: the self-fetching data hook behind TxnDetail's Receipts section
// (GET/POST/DELETE /api/transactions/{id}/receipts). Covers the load/error states, upload()
// (one direct POST to .../receipts/upload — no generic-attachment detour), unlink(), and the
// stale-response ref-guard — the drawer never unmounts on a row switch, so a slow response
// for a txnId the caller has since navigated away from must not clobber the newer row's list.

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
}))

import { useTransactionReceipts } from './useTransactionReceipts'
import { authFetch } from '../api/client'

const mockAuthFetch = vi.mocked(authFetch)

function jsonResponse(body: unknown, ok = true, status = 200) {
  return { ok, status, json: async () => body } as Response
}

const WIRE_RECEIPT = {
  receipt_id: 'rcpt_1',
  link_id: 'trcpt_1',
  filename: 'lunch.jpg',
  media_type: 'image/jpeg',
  created_at: '2026-08-01T00:00:00Z',
  linked_at: '2026-08-01T00:00:01Z',
  source_attachment_id: 'att_1',
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('useTransactionReceipts', () => {
  it('loads and maps the receipts list on mount', async () => {
    mockAuthFetch.mockResolvedValue(jsonResponse({ receipts: [WIRE_RECEIPT] }))

    const { result } = renderHook(() => useTransactionReceipts('txn_1'))
    expect(result.current.status).toBe('loading')

    await waitFor(() => expect(result.current.status).toBe('ok'))
    expect(result.current.receipts).toEqual([
      {
        receiptId: 'rcpt_1',
        linkId: 'trcpt_1',
        filename: 'lunch.jpg',
        mediaType: 'image/jpeg',
        createdAt: '2026-08-01T00:00:00Z',
        linkedAt: '2026-08-01T00:00:01Z',
        sourceAttachmentId: 'att_1',
      },
    ])
    expect(mockAuthFetch).toHaveBeenCalledWith('/api/transactions/txn_1/receipts')
  })

  it('sets status=error on a failed list fetch', async () => {
    mockAuthFetch.mockResolvedValue(jsonResponse({}, false, 500))

    const { result } = renderHook(() => useTransactionReceipts('txn_1'))
    await waitFor(() => expect(result.current.status).toBe('error'))
    expect(result.current.receipts).toEqual([])
  })

  it('upload() posts the file directly and refreshes the list', async () => {
    mockAuthFetch
      .mockResolvedValueOnce(jsonResponse({ receipts: [] })) // initial list load
      .mockResolvedValueOnce(jsonResponse({ receipt: {}, link: {} })) // POST .../receipts/upload
      .mockResolvedValueOnce(jsonResponse({ receipts: [WIRE_RECEIPT] })) // refresh

    const { result } = renderHook(() => useTransactionReceipts('txn_1'))
    await waitFor(() => expect(result.current.status).toBe('ok'))

    const file = new File(['bytes'], 'lunch.jpg', { type: 'image/jpeg' })
    await result.current.upload(file)

    expect(mockAuthFetch).toHaveBeenNthCalledWith(
      2,
      '/api/transactions/txn_1/receipts/upload',
      expect.objectContaining({ method: 'POST' }),
    )
    await waitFor(() => expect(result.current.receipts).toHaveLength(1))
  })

  it('upload() rejects when the upload POST fails, without swallowing the error', async () => {
    mockAuthFetch
      .mockResolvedValueOnce(jsonResponse({ receipts: [] }))
      .mockResolvedValueOnce(jsonResponse({}, false, 422))

    const { result } = renderHook(() => useTransactionReceipts('txn_1'))
    await waitFor(() => expect(result.current.status).toBe('ok'))

    const file = new File(['bytes'], 'lunch.jpg', { type: 'image/jpeg' })
    await expect(result.current.upload(file)).rejects.toThrow()
  })

  it('unlink() deletes the link and refreshes the list', async () => {
    mockAuthFetch
      .mockResolvedValueOnce(jsonResponse({ receipts: [WIRE_RECEIPT] })) // initial load
      .mockResolvedValueOnce(jsonResponse({ unlinked: 'trcpt_1' })) // DELETE
      .mockResolvedValueOnce(jsonResponse({ receipts: [] })) // refresh

    const { result } = renderHook(() => useTransactionReceipts('txn_1'))
    await waitFor(() => expect(result.current.receipts).toHaveLength(1))

    await result.current.unlink('rcpt_1')

    expect(mockAuthFetch).toHaveBeenNthCalledWith(
      2,
      '/api/transactions/txn_1/receipts/rcpt_1',
      expect.objectContaining({ method: 'DELETE' }),
    )
    await waitFor(() => expect(result.current.receipts).toHaveLength(0))
  })

  it('ignores a list response for a txnId the hook has since navigated away from', async () => {
    let resolveFirst: (r: Response) => void = () => {}
    const firstPromise = new Promise<Response>((resolve) => {
      resolveFirst = resolve
    })
    mockAuthFetch
      .mockReturnValueOnce(firstPromise) // slow response for txn_1
      .mockResolvedValueOnce(jsonResponse({ receipts: [] })) // fast response for txn_2

    const { result, rerender } = renderHook(({ id }) => useTransactionReceipts(id), {
      initialProps: { id: 'txn_1' },
    })
    rerender({ id: 'txn_2' })
    await waitFor(() => expect(result.current.status).toBe('ok'))

    // The stale txn_1 response finally resolves — must NOT clobber txn_2's already-loaded view.
    resolveFirst(jsonResponse({ receipts: [WIRE_RECEIPT] }))
    await new Promise((r) => setTimeout(r, 0))
    expect(result.current.receipts).toEqual([])
  })
})
