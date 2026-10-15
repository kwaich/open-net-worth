/**
 * onwDataSource.ts — ONW reference DataSourceAdapter implementation.
 *
 * This file intentionally carries ONW-domain imports (@/api/client) — it is the
 * one "host adapter" that wires the core to ONW's FastAPI backend. It lives in
 * lucius/ for co-location but is NOT in the core module set (check-entity-rows.mjs
 * enforces the core import-cleanliness on the CORE_MODULE_SET; this file is
 * explicitly excluded from that set).
 *
 * Wire once at the app root: wireDataSource(onwDataSource)
 * (see RFC §14.2.1 for the reference adapter pattern).
 */

import { authFetch } from '../api/client'
import type {
  DataSourceAdapter,
  MentionQuery,
  MentionSearchResult,
  ResolvedEntity,
} from './adapters'
import type { MentionRow } from './mentionRow'
import type { ClientDescriptor } from './entityRegistry'
import { iconFor, toClientDescriptors } from './entityRegistry'

/** The descriptors the server embedded in index.html (backend/main.py
 *  `_shell_with_entity_types`) — the synchronous first-paint seed. Empty when absent
 *  (the Vite dev server serves its own index.html) or malformed; the
 *  /api/entity-types refresh then fills the registry. */
export function readEmbeddedDescriptors(doc: Document = document): ClientDescriptor[] {
  const el = doc.getElementById('onw-entity-types')
  if (!el?.textContent) return []
  try {
    return toClientDescriptors(JSON.parse(el.textContent))
  } catch {
    return []
  }
}

export const onwDataSource: DataSourceAdapter = {
  async search(q: MentionQuery): Promise<MentionSearchResult> {
    const params = new URLSearchParams()
    if (q.q) params.set('q', q.q)
    if (q.surfaces?.length) params.set('surface', q.surfaces.join(','))
    if (q.types?.length) params.set('types', q.types.join(','))
    params.set('limit', String(q.limit ?? 40))
    if (q.cursor) params.set('cursor', q.cursor)

    const res = await authFetch(`/api/mentionables?${params}`, { signal: q.signal })
    if (!res.ok) throw new Error(`mentionables ${res.status}`)
    const data: { items: MentionRow[]; next_cursor: string | null } = await res.json()
    return {
      items: (data.items ?? []).map((item) => ({
        ...item,
        icon: item.icon || iconFor(item.type),
      })),
      nextCursor: data.next_cursor ?? null,
    }
  },

  async resolve(refs): Promise<ResolvedEntity[]> {
    const res = await authFetch('/api/mentions/resolve', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ refs }),
    })
    if (!res.ok) throw new Error(`resolve ${res.status}`)
    // POST /api/mentions/resolve returns a BARE array [{type,ref,label,data|error}] (handler
    // return type list[dict]) — NOT {items:[...]} like the /api/mentionables search endpoint.
    // Reading `.items` off the array silently dropped every resolved record, so the detail
    // popup never upgraded past its sparse fallback. Accept the array (or a wrapped shape).
    const data: unknown = await res.json()
    if (Array.isArray(data)) return data as ResolvedEntity[]
    return (data as { items?: ResolvedEntity[] } | null)?.items ?? []
  },

  async descriptors(): Promise<ClientDescriptor[]> {
    const res = await authFetch('/api/entity-types')
    if (!res.ok) return []
    return toClientDescriptors(await res.json())
  },
}
