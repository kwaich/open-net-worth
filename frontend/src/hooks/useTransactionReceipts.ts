import { useCallback, useEffect, useRef, useState } from 'react'
import { useDataVersion } from './useDataVersion'
import { authFetch } from '../api/client'

export interface ReceiptEntry {
  receiptId: string
  linkId: string
  filename: string
  mediaType: string | null
  createdAt: string
  linkedAt: string
  sourceAttachmentId: string | null
}

interface ReceiptEntryWire {
  receipt_id: string
  link_id: string
  filename: string | null
  media_type: string | null
  created_at: string | null
  linked_at: string | null
  source_attachment_id: string | null
}

function fromWire(row: ReceiptEntryWire): ReceiptEntry {
  return {
    receiptId: row.receipt_id,
    linkId: row.link_id,
    filename: row.filename || 'receipt',
    mediaType: row.media_type,
    createdAt: row.created_at || '',
    linkedAt: row.linked_at || '',
    sourceAttachmentId: row.source_attachment_id,
  }
}

interface UseTransactionReceiptsResult {
  status: 'loading' | 'ok' | 'error'
  receipts: ReceiptEntry[]
  upload: (file: File) => Promise<void>
  unlink: (receiptId: string) => Promise<void>
  refresh: () => void
}

/** Self-fetching Receipts-section data hook — GET/POST/DELETE
 *  /api/transactions/{txnId}/receipts. Keyed on txnId with a stale-response ref-guard: the
 *  drawer never unmounts on a row switch (TxnDetail.tsx just swaps its `txn` prop in place),
 *  so a slow response for a txnId the caller has since navigated away from must not clobber
 *  the newer row's list — same pattern TxnDetail.tsx's own loadSourceEmails/
 *  onLoadStatementLinks effects already use. */
export function useTransactionReceipts(txnId: string): UseTransactionReceiptsResult {
  const [status, setStatus] = useState<'loading' | 'ok' | 'error'>('loading')
  const [receipts, setReceipts] = useState<ReceiptEntry[]>([])
  const activeIdRef = useRef(txnId)
  useEffect(() => {
    activeIdRef.current = txnId
  }, [txnId])

  const load = useCallback(
    (quiet = false) => {
      const id = txnId
      if (!quiet) setStatus('loading')
      authFetch(`/api/transactions/${id}/receipts`)
        .then(async (r) => {
          if (!r.ok) throw new Error(`HTTP ${r.status}`)
          return r.json()
        })
        .then((body: { receipts?: ReceiptEntryWire[] }) => {
          if (activeIdRef.current !== id) return
          setReceipts((body.receipts ?? []).map(fromWire))
          setStatus('ok')
        })
        .catch(() => {
          if (activeIdRef.current !== id) return
          setStatus('error')
        })
    },
    [txnId],
  )
  // attach/unlink/delete_receipt from Lucius → refetch this drawer's receipts quietly.
  const anyWriteV = useDataVersion('*')
  useEffect(() => {
    load(anyWriteV > 0)
  }, [load, anyWriteV])

  const upload = useCallback(
    async (file: File) => {
      // One direct save, not "upload as a generic chat attachment, then promote it" — the
      // drawer picks a file that's ALREADY meant to be a receipt, so it goes straight to
      // ReceiptStore with no parsing detour (see backend/services/receipts.py::_upload).
      const id = txnId
      const form = new FormData()
      form.append('file', file)
      const res = await authFetch(`/api/transactions/${id}/receipts/upload`, {
        method: 'POST',
        body: form,
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      if (activeIdRef.current === id) load()
    },
    [txnId, load],
  )

  const unlink = useCallback(
    async (receiptId: string) => {
      const id = txnId
      const res = await authFetch(`/api/transactions/${id}/receipts/${receiptId}`, {
        method: 'DELETE',
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      if (activeIdRef.current === id) load()
    },
    [txnId, load],
  )

  return { status, receipts, upload, unlink, refresh: load }
}
