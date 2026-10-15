import { authFetch } from './client'
import { fetchCachedQuery, invalidateQueryCache } from '../state/queryCache'
import { MATCH_REVIEWS_KEY, fetchJson } from '../hooks/serverState'

// Typed fetch wrappers for the dedup/match REST surface.
//
// Mirrors backend/api/matches.py:
//   GET  /api/matches/reviews
//   POST /api/matches/reviews/{id}/accept
//   POST /api/matches/reviews/{id}/dismiss
//   POST /api/matches/reviews/{id}/swap-canonical
//   POST /api/matches/judge
//   POST /api/matches/merge
//   POST /api/matches/undo
//   GET/PUT /api/matches/policy
//   GET /api/matches/decisions
//   DELETE /api/matches/decisions/{signature}

// ── Shapes ────────────────────────────────────────────────────────────────────

export interface MatchTxn {
  id: string
  date: string
  /** Bank/matching string (ADR 2026-07-17) — the transaction dict is spread raw onto this wire
   *  shape, so this mirrors `Transaction.description` exactly (never `note`, which no longer
   *  exists on the stored row). */
  description: string
  /** Personal annotation — display-only, rides along for completeness. */
  memo?: string
  amount: number
  currency: string
  kind: string
  from_account_id: string | null
  to_account_id: string | null
  source: string | null
  /** Provenance/dedup pairs (the raw txn dict is spread onto this wire shape; ONE list,
   *  [0] = the creation-time origin — ADR 2026-07-21). */
  external_refs: Array<{ source: string | null; ref: string }>
}

export interface MatchReview {
  id: string
  klass: 'near_duplicate' | 'transfer_pair'
  score: number
  become_transfer: boolean
  reasons: string[]
  llm_reason: string | null
  batch_id: string | null
  batch_description: string | null
  batch_created_at: string | null
  status: 'pending' | 'accepted' | 'dismissed'
  incoming: MatchTxn | null
  canonical: MatchTxn | null
  alternatives?: MatchTxn[]
  signature: string
}

export interface MatchReviewsResponse {
  reviews: MatchReview[]
  groups: Record<string, MatchReview[]>
  count: number
  superseded_count?: number
}

export interface MatchDecision {
  signature: string
  verdict: 'same' | 'different' | 'pair'
  canonical_id: string | null
  become_transfer: boolean
  llm_reason: string | null
  source: 'llm' | 'user' | 'rule'
  created_at: string
  payee_label: string | null
  amount_bucket: string | null
}

export interface MatchDecisionsResponse {
  decisions: MatchDecision[]
  total: number
  limit: number
}

export type DedupPolicy = 'review_all' | 'auto_transfers' | 'auto_high'

// ── Helpers ───────────────────────────────────────────────────────────────────

async function asJson<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      const data = await r.json()
      if (typeof data?.detail === 'string') detail = data.detail
    } catch {
      // non-JSON body — keep the generic message
    }
    throw new Error(detail)
  }
  return r.json() as Promise<T>
}

const jsonBody = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

// ── Reviews ───────────────────────────────────────────────────────────────────

export async function listMatchReviews(): Promise<MatchReviewsResponse> {
  // Shares the cache entry with RouterShell's sidebar dedup badge — before consolidation the
  // review list and the badge were two independent readers of this endpoint, visible in the
  // logs as back-to-back duplicate /api/matches/reviews requests on every page load.
  return fetchCachedQuery<MatchReviewsResponse>(MATCH_REVIEWS_KEY, () =>
    fetchJson<MatchReviewsResponse>(MATCH_REVIEWS_KEY),
  )
}

export async function acceptReview(id: string): Promise<{ ok: boolean; canonical: MatchTxn }> {
  return asJson<{ ok: boolean; canonical: MatchTxn }>(
    await authFetch(`/api/matches/reviews/${encodeURIComponent(id)}/accept`, { method: 'POST' }),
  )
}

// ── Recent merges (UX-3) ──────────────────────────────────────────────────────

export interface RecentMerge {
  absorbed_id: string
  canonical_id: string
  canonical_description: string
  absorbed_description: string
  canonical_amount: number | null
  absorbed_amount: number | null
  canonical_currency: string
  merged_at: string
  source: 'user' | 'rule' | 'ai'
}

export interface RecentMergesResponse {
  merges: RecentMerge[]
  count: number
}

export async function listRecentMerges(days = 7, limit = 20): Promise<RecentMergesResponse> {
  return asJson<RecentMergesResponse>(
    await authFetch(`/api/matches/merges/recent?days=${days}&limit=${limit}`),
  )
}

export async function dismissReview(id: string): Promise<{ ok: boolean }> {
  return asJson<{ ok: boolean }>(
    await authFetch(`/api/matches/reviews/${encodeURIComponent(id)}/dismiss`, { method: 'POST' }),
  )
}

// ── Bulk judge ────────────────────────────────────────────────────────────────

export async function judgeMatches(): Promise<{
  auto_merged: number
  kept_for_review: number
  judged: number
  cost_capped: boolean
}> {
  return asJson<{
    auto_merged: number
    kept_for_review: number
    judged: number
    cost_capped: boolean
  }>(await authFetch('/api/matches/judge', { method: 'POST' }))
}

// ── Merge / undo ──────────────────────────────────────────────────────────────

export async function mergeTransactions(
  canonical_id: string,
  absorbed_id: string,
  become_transfer?: boolean,
  reason?: string,
): Promise<{ ok: boolean }> {
  return asJson<{ ok: boolean }>(
    await authFetch(
      '/api/matches/merge',
      jsonBody('POST', { canonical_id, absorbed_id, become_transfer, reason }),
    ),
  )
}

export async function undoMerge(absorbed_id: string): Promise<{ ok: boolean }> {
  return asJson<{ ok: boolean }>(
    await authFetch('/api/matches/undo', jsonBody('POST', { absorbed_id })),
  )
}

// ── Policy ────────────────────────────────────────────────────────────────────

export async function getPolicy(): Promise<{ policy: DedupPolicy; options: DedupPolicy[] }> {
  return asJson<{ policy: DedupPolicy; options: DedupPolicy[] }>(
    await authFetch('/api/matches/policy'),
  )
}

export async function setPolicy(policy: DedupPolicy): Promise<{ policy: DedupPolicy }> {
  return asJson<{ policy: DedupPolicy }>(
    await authFetch('/api/matches/policy', jsonBody('PUT', { policy })),
  )
}

// ── Config (policy + L3 AI judgment settings) ─────────────────────────────────

export interface DedupConfig {
  policy: DedupPolicy
  policy_options: string[]
  l3_enabled: boolean
  l3_model: string
  l3_max_per_run: number
}

export async function getDedupConfig(): Promise<DedupConfig> {
  return fetchCachedQuery(
    '/api/matches/config',
    async () => asJson<DedupConfig>(await authFetch('/api/matches/config')),
    { staleTime: 0 },
  )
}

export async function setDedupConfig(
  patch: Partial<Pick<DedupConfig, 'policy' | 'l3_enabled' | 'l3_model' | 'l3_max_per_run'>>,
): Promise<DedupConfig> {
  const result = await asJson<DedupConfig>(
    await authFetch('/api/matches/config', jsonBody('PUT', patch)),
  )
  invalidateQueryCache('/api/matches/config')
  return result
}

// ── Swap canonical (F20) ──────────────────────────────────────────────────────

export async function swapCanonical(id: string, canonical_id: string): Promise<{ ok: boolean }> {
  return asJson<{ ok: boolean }>(
    await authFetch(
      `/api/matches/reviews/${encodeURIComponent(id)}/swap-canonical`,
      jsonBody('POST', { canonical_id }),
    ),
  )
}

// ── Decisions (F04) ───────────────────────────────────────────────────────────

export async function listMatchDecisions(limit = 50): Promise<MatchDecisionsResponse> {
  const key = `/api/matches/decisions?limit=${limit}`
  return fetchCachedQuery(key, async () => asJson<MatchDecisionsResponse>(await authFetch(key)), {
    staleTime: 0,
  })
}

export async function forgetMatchDecision(signature: string): Promise<{ ok: boolean }> {
  const result = await asJson<{ ok: boolean }>(
    await authFetch(`/api/matches/decisions/${encodeURIComponent(signature)}`, {
      method: 'DELETE',
    }),
  )
  invalidateQueryCache('/api/matches/decisions')
  return result
}
