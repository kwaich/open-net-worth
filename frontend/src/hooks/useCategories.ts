/**
 * useCategories.ts — Shared hook for the server's canonical category list.
 *
 * Fetches GET /api/categories (visible built-ins + custom categories).
 * Both the Accounts tab (via useAccountsLedger) and the Budget tab (useBudget)
 * share this hook so the fetch logic and type definitions live in one place.
 */

import { useCallback, useMemo } from 'react'
import { authFetch } from '../api/client'
import { useQueryCache } from '../state/queryCache'

// Server category row shape — mirrors backend services.categories.CategoryRow. `id` is an
// OPAQUE, IMMUTABLE id (`cat_<12hex>`, minted server-side — see docs/adr/2026-07-05-category-
// opaque-ids.md); `label` is the only human-readable field. There is no more built-in/custom
// distinction — every category (including former built-ins) is plain, user-editable data, so
// there is no client-derived `builtin` flag anymore.
export interface CategoryMeta {
  id: string
  label: string
  kind: 'income' | 'expense' | string
  color: string
}

// Structural ids cannot be renamed/deleted/hidden — mirror of
// backend.services.categories.STRUCTURAL_KEYS. The split sentinel is the ONLY structural
// category (and the one category id that is a literal string rather than an opaque `cat_xxx`);
// every other category, including former built-ins, is pure data and fully user-deletable.
export const STRUCTURAL_CATEGORY_KEYS = new Set(['split'])
const EMPTY_CATEGORIES: CategoryMeta[] = []

export function useCategories() {
  const url = '/api/categories'
  const query = useQueryCache(
    url,
    async () => {
      const response = await authFetch(url)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const data = await response.json()
      if (!Array.isArray(data)) throw new Error('Invalid categories response')
      return data as CategoryMeta[]
    },
    { staleTime: 0 },
  )
  const categories = query.data ?? EMPTY_CATEGORIES
  const refreshQuery = query.refresh
  const reload = useCallback(() => {
    void refreshQuery()
  }, [refreshQuery])

  const catMap = useMemo(
    () => Object.fromEntries(categories.map((c) => [c.id, c])) as Record<string, CategoryMeta>,
    [categories],
  )

  return {
    categories,
    catMap,
    loading: query.initialLoading,
    error: query.error instanceof Error ? query.error.message : null,
    reload,
  }
}
