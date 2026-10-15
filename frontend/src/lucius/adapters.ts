/**
 * adapters.ts — DataSourceAdapter seam for the Lucius mention engine.
 *
 * Defines the adapter interface that decouples the core mention engine from
 * any specific backend. ONW wires onwDataSource at the app root; a no-backend
 * host can supply a local/in-DOM adapter.
 *
 * Extraction-readiness (§14.8 R-EX2): this module has NO ONW-domain imports.
 * The adapter implementations (onwDataSource.ts) live separately and carry
 * the ONW-specific imports (@/api). The core engine only imports this interface.
 *
 * See RFC §14.2 for the full four-adapter contract. This file covers the
 * DataSourceAdapter — the only one wired in Phase 1.
 */

import type { MentionRow } from './mentionRow'
import type { ClientDescriptor } from './entityRegistry'

// ── DataSourceAdapter ─────────────────────────────────────────────────────────

export interface MentionQuery {
  q: string
  surfaces?: string[]
  types?: string[]
  limit?: number
  cursor?: string | null
  signal?: AbortSignal
}

export interface MentionSearchResult {
  items: MentionRow[]
  nextCursor?: string | null
}

export interface ResolvedEntity {
  type: string
  ref: string
  label: string
  data?: Record<string, string | number | null>
  error?: string
}

/**
 * DataSourceAdapter — the ONLY external seam the core mention engine uses.
 *
 * ONW's implementation calls /api/mentionables + /api/mentions/resolve.
 * A no-backend host may return [] from search() and let the DOM harvest cover it.
 * A local in-DOM host's resolve() reads back data-ctx-fields from the DOM element.
 */
export interface DataSourceAdapter {
  /**
   * Search for mentionable entities matching the query.
   * The core also harvests the DOM + recents and merges both lists;
   * a backend-less search() may return [] and rely on the DOM harvest alone.
   */
  search(query: MentionQuery): Promise<MentionSearchResult>

  /**
   * Resolve a list of entity refs to their full detail records.
   * Used by resolve-on-tap (MentionDetail) and chat-turn context building.
   * The core fences + sensitive-filters the result on ingress.
   */
  resolve(refs: { type: string; ref: string; label?: string }[]): Promise<ResolvedEntity[]>

  /**
   * Optional: fetch the descriptor registry (icon/label/tone/fields).
   * ONW's GET /api/entity-types is this call. Omit → core keeps the host-seeded descriptors.
   */
  descriptors?(): Promise<ClientDescriptor[]>
}

// ── Module-level adapter store ────────────────────────────────────────────────

let _dataSource: DataSourceAdapter | null = null

/**
 * Wire the DataSourceAdapter at the app root (called once in App.tsx or main.tsx).
 * Before this is called, search() returns empty and resolve() returns not_found.
 */
export function wireDataSource(adapter: DataSourceAdapter): void {
  _dataSource = adapter
}

/** The currently wired DataSourceAdapter, or null if not yet wired. */
export function getDataSource(): DataSourceAdapter | null {
  return _dataSource
}
