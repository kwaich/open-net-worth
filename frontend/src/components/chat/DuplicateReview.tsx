import { useEffect, useState } from 'react'
import type { PotentialDuplicate } from '../../types/chat'
import { authFetch } from '../../api/client'
import { ACCOUNTS_KEY, fetchJson } from '../../hooks/serverState'
import { fetchCachedQuery } from '../../state/queryCache'
import { fDate } from '../../utils/format'

interface Props {
  duplicates: PotentialDuplicate[]
  batchId?: string
  onSendMessage: (text: string) => void
}

export function DuplicateReview({ duplicates, onSendMessage }: Props) {
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [status, setStatus] = useState<'idle' | 'deleting' | 'done'>('idle')
  const [deletedIds, setDeletedIds] = useState<Set<string>>(new Set())
  const [accounts, setAccounts] = useState<Record<string, string>>({})

  useEffect(() => {
    fetchCachedQuery<
      { accounts?: { id: string; name: string }[] } | { id: string; name: string }[]
    >(ACCOUNTS_KEY, () =>
      fetchJson<{ accounts?: { id: string; name: string }[] } | { id: string; name: string }[]>(
        ACCOUNTS_KEY,
      ),
    )
      .then(
        (data: { accounts?: { id: string; name: string }[] } | { id: string; name: string }[]) => {
          const list = Array.isArray(data) ? data : (data.accounts ?? [])
          const map: Record<string, string> = {}
          for (const a of list) map[a.id] = a.name
          setAccounts(map)
        },
      )
      .catch(() => {})
  }, [])

  function acctName(id?: string | null) {
    if (!id) return null
    return accounts[id] ?? id.slice(0, 8)
  }

  function toggle(id: string) {
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAll() {
    const visible = duplicates.filter((d) => !deletedIds.has(d.id)).map((d) => d.id)
    if (checked.size === visible.length) setChecked(new Set())
    else setChecked(new Set(visible))
  }

  async function deleteSelected() {
    if (checked.size === 0) return
    setStatus('deleting')
    const ids = [...checked]
    const failed: string[] = []
    for (const id of ids) {
      try {
        const res = await authFetch(`/api/transactions/${id}`, { method: 'DELETE' })
        if (!res.ok) failed.push(id)
        else setDeletedIds((prev) => new Set([...prev, id]))
      } catch {
        failed.push(id)
      }
    }
    setChecked(new Set())
    setStatus('done')
    const deleted = ids.length - failed.length
    if (deleted > 0) {
      onSendMessage(
        `Deleted ${deleted} duplicate transaction${deleted > 1 ? 's' : ''}${failed.length ? ` (${failed.length} failed)` : ''}.`,
      )
    }
  }

  function keepAll() {
    setStatus('done')
    onSendMessage('Keeping all flagged transactions — they are intentional.')
  }

  const visible = duplicates.filter((d) => !deletedIds.has(d.id))

  if (status === 'done' && visible.length === 0) {
    return <div className="dup-review dup-review--done">✓ Duplicate review complete</div>
  }

  return (
    <div className="dup-review">
      <div className="dup-review-header">
        <span className="dup-review-title">⚠ Potential duplicates — {visible.length} flagged</span>
        <span className="dup-review-hint">
          These were inserted but match existing transactions. Select any to delete.
        </span>
      </div>

      <div className="dup-review-table-wrap">
        <table className="dup-review-table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={checked.size === visible.length && visible.length > 0}
                  onChange={toggleAll}
                />
              </th>
              <th>Date</th>
              <th>Description</th>
              <th>Amount</th>
              <th>From</th>
              <th>To</th>
              <th>ID</th>
              <th>Copies</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((d) => {
              const from = acctName(d.from_account_id)
              const to = acctName(d.to_account_id)
              return (
                <tr key={d.id} className={checked.has(d.id) ? 'dup-row--selected' : ''}>
                  <td>
                    <input
                      type="checkbox"
                      checked={checked.has(d.id)}
                      onChange={() => toggle(d.id)}
                    />
                  </td>
                  <td className="dup-cell-date">{fDate(d.date, 'numeric')}</td>
                  <td className="dup-cell-desc">{d.description || '—'}</td>
                  <td className="dup-cell-amount">{d.amount?.toLocaleString()}</td>
                  <td className="dup-cell-acct">
                    {from ?? <span className="dup-cell-null">—</span>}
                  </td>
                  <td className="dup-cell-acct">
                    {to ?? <span className="dup-cell-null">—</span>}
                  </td>
                  <td className="dup-cell-id">{d.id.slice(0, 8)}</td>
                  <td className="dup-cell-count">{d.duplicates_count}×</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {status === 'done' && visible.length > 0 && (
        <div className="dup-review-partial">✓ Some deleted — remaining entries kept.</div>
      )}

      <div className="dup-review-actions">
        <button
          className="dup-delete-btn"
          onClick={deleteSelected}
          disabled={checked.size === 0 || status === 'deleting'}
        >
          {status === 'deleting' ? 'Deleting…' : `Delete selected (${checked.size})`}
        </button>
        <button className="dup-keep-btn" onClick={keepAll} disabled={status === 'deleting'}>
          Keep all
        </button>
      </div>
    </div>
  )
}
