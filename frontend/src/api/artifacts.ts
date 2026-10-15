import { authFetch } from './client'
import type { ArtifactRef, ArtifactType } from '../types/chat'

// Chat-artifact fetch wrapper. Mirrors backend/api/artifacts.py:
// GET /api/artifacts/{id} serves the raw document source as text/plain (+ nosniff
// + CSP sandbox) so it can never execute same-origin; it backs the viewer's source tab and
// existence check. The rendered document is GET /api/artifacts/{id}/frame, loaded as
// the sandboxed iframe's `src` (never `srcdoc`: that would inherit the page's CSP and stop the
// artifact's inline script). authFetch sets X-Requested-With, satisfying the CSRF guard.

export async function fetchArtifactContent(id: string): Promise<string> {
  const res = await authFetch(`/api/artifacts/${encodeURIComponent(id)}`)
  if (!res.ok) throw new Error(`artifact fetch failed (${res.status})`)
  return res.text()
}

/** A pdf/xlsx/csv artifact as bytes — through authFetch (session + CSRF header), with the
 * filename the server chose in Content-Disposition (null when it sent none). */
export async function fetchArtifactFile(
  id: string,
): Promise<{ blob: Blob; filename: string | null }> {
  const res = await authFetch(`/api/artifacts/${encodeURIComponent(id)}/download`)
  if (!res.ok) throw new Error(`artifact download failed (${res.status})`)
  return {
    blob: await res.blob(),
    filename: filenameFromDisposition(res.headers.get('Content-Disposition')),
  }
}

export function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null
  const star = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header)
  if (star) {
    try {
      return decodeURIComponent((star[1] ?? '').trim())
    } catch {
      /* fall through to the plain filename */
    }
  }
  const plain = /filename\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;]+))/i.exec(header)
  if (!plain) return null
  return (
    (plain[1] !== undefined ? plain[1].replace(/\\(.)/g, '$1') : (plain[2] ?? '').trim()) || null
  )
}

/** A recent-artifact list row — ``ArtifactRef`` plus the ``createdAt`` a picker
 * needs to label/sort options; ``ArtifactRef`` itself stays exactly what
 * ``ArtifactCard`` already expects. */
export interface RecentArtifact extends ArtifactRef {
  createdAt?: string
}

/** Newest-first artifact metadata across every session — never the document
 * bytes. Backs the Overview `lux-html` widget's "pick a recent artifact"
 * picker, which (unlike the chat panel) has no one session to scope to. */
export async function fetchRecentArtifacts(limit = 20): Promise<RecentArtifact[]> {
  const res = await authFetch(`/api/artifacts?limit=${encodeURIComponent(String(limit))}`)
  if (!res.ok) throw new Error(`artifact list fetch failed (${res.status})`)
  const body = (await res.json()) as {
    artifacts: Array<{
      id: string
      title: string
      filename: string
      artifact_type: ArtifactType
      size_bytes?: number
      created_at?: string
    }>
  }
  return body.artifacts.map((a) => ({
    id: a.id,
    title: a.title,
    filename: a.filename,
    artifactType: a.artifact_type,
    sizeBytes: a.size_bytes,
    createdAt: a.created_at,
  }))
}
