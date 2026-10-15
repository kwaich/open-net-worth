// BackgroundStatementNotices — the in-app popup for a statement read that finished while the
// owner was elsewhere (owner, 2026-10-06: "notification when a background statement read
// finishes … at least in app as a popup").
//
// Mounted once, app-wide (TransactionSurfacesProvider), so it works from any tab. It polls the
// existing `GET /api/statements/{id}/parse-status` for every statement in the
// backgroundStatements store — only while the page is visible — and, when a read reaches a
// terminal state, stops tracking it and shows one popup per statement until dismissed. "Review"
// hands the statement to the Accounts tab (requestStatementReview) and asks the shell to go there.

import { useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { useDocumentVisible } from '../../../hooks/useImportProgress'
import { X } from '../../../ui/icons'
import {
  backgroundStatements,
  requestStatementReview,
  subscribeBackgroundStatements,
  untrackBackgroundStatement,
  type BackgroundStatement,
} from './backgroundStatements'

type Finished = BackgroundStatement & { status: 'ready' | 'failed' | 'interrupted' }

const POLL_MS = 4000
const TERMINAL = new Set(['ready', 'failed', 'interrupted'])

export function BackgroundStatementNotices({ onOpenAccounts }: { onOpenAccounts?: () => void }) {
  const [tracked, setTracked] = useState(backgroundStatements)
  const [finished, setFinished] = useState<Finished[]>([])
  const visible = useDocumentVisible()

  useEffect(() => subscribeBackgroundStatements(() => setTracked(backgroundStatements())), [])

  useEffect(() => {
    if (!tracked.length || !visible) return
    let cancelled = false
    const poll = async () => {
      for (const s of tracked) {
        try {
          const r = await authFetch(`/api/statements/${s.id}/parse-status`)
          if (r.status === 404) {
            untrackBackgroundStatement(s.id) // the statement was deleted meanwhile
            continue
          }
          if (!r.ok) continue
          const body = (await r.json()) as { parse_status?: string | null }
          const status = body.parse_status
          if (!status || !TERMINAL.has(status)) continue
          untrackBackgroundStatement(s.id)
          if (cancelled) return
          setFinished((list) => [
            ...list.filter((f) => f.id !== s.id),
            { ...s, status: status as Finished['status'] },
          ])
        } catch {
          /* offline / transient — the next tick retries */
        }
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [tracked, visible])

  if (!finished.length) return null
  const dismiss = (id: string) => setFinished((list) => list.filter((f) => f.id !== id))
  return (
    <div className="bgst-stack mb-anchored" role="status" aria-live="polite">
      {finished.map((f) => {
        // The app's own toast pill (acct-toast / as-toast / ov-toast): one line, no card, no
        // colour bar — the state is in the words.
        const title =
          f.status === 'ready'
            ? 'Ready to review'
            : f.status === 'failed'
              ? 'Couldn’t read'
              : 'Reading stopped'
        return (
          <div key={f.id} className="bgst-note">
            <span className="bgst-text">
              <span className="bgst-title">{title}</span>
              <span className="bgst-detail">{f.filename || 'your statement'}</span>
            </span>
            {f.status === 'ready' && (
              <button
                type="button"
                className="bgst-review"
                onClick={() => {
                  requestStatementReview({ id: f.id, accountId: f.accountId, filename: f.filename })
                  onOpenAccounts?.()
                  dismiss(f.id)
                }}
              >
                Review
              </button>
            )}
            <button
              type="button"
              className="bgst-x"
              aria-label="Dismiss"
              onClick={() => dismiss(f.id)}
            >
              <X size={14} strokeWidth={2} aria-hidden="true" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
