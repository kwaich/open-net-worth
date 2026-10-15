import { authFetch } from './client'
import type { ProblemReport } from '../utils/problemReport'

// The browser's half of "Report a problem". The server assembles and redacts the draft (it owns
// the logs and the book's names); nothing is sent until the user presses Send on the previewed text.

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export async function fetchReportDraft(error: string | undefined): Promise<ProblemReport> {
  const res = await authFetch('/api/problem-reports/draft', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      error: error?.slice(0, 4000) || null,
      screen: window.location.pathname.slice(0, 256),
      include_logs: true,
    }),
  })
  if (!res.ok) throw new Error(`report draft failed (${res.status})`)
  return (await res.json()) as ProblemReport
}

/** Posts exactly `report`. A refusal comes back as the server's own plain sentence. */
export async function sendProblemReport(report: ProblemReport): Promise<{ id: string | null }> {
  const res = await authFetch('/api/problem-reports/send', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(report),
  })
  const body = (await res.json().catch(() => null)) as {
    id?: string | null
    detail?: unknown
  } | null
  if (!res.ok) {
    throw new Error(
      typeof body?.detail === 'string'
        ? body.detail
        : 'The report could not be sent. Check the description and email address.',
    )
  }
  return { id: body?.id ?? null }
}
