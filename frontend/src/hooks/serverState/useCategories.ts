import { useDataVersion } from '../useDataVersion'
import { type QueryCacheResult, useQueryCache } from '../../state/queryCache'
import { fetchJson } from './fetchJson'
import { CATEGORIES_KEY } from './keys'

export interface CategoryLike {
  id: string
  label?: string
  kind?: string
  [key: string]: unknown
}

const fetchCategories = () => fetchJson<CategoryLike[]>(CATEGORIES_KEY)
const fetchCategoriesWithHidden = () =>
  fetchJson<CategoryLike[]>(`${CATEGORIES_KEY}?include_hidden=true`)

/**
 * The shared `/api/categories` read (6 independent call sites before consolidation).
 *
 * `includeHidden` selects a DIFFERENT key, not a client-side filter of one superset: the two
 * responses genuinely differ, and serving a hidden-inclusive list to a caller that asked for
 * the visible one would silently change what the user sees. Two keys, two entries, each
 * still shared across all of its own callers.
 */
export function useCategories<T extends CategoryLike = CategoryLike>(
  includeHidden = false,
): QueryCacheResult<T[]> {
  const version = useDataVersion('categories')
  const key = includeHidden ? `${CATEGORIES_KEY}?include_hidden=true` : CATEGORIES_KEY
  const fetcher = includeHidden ? fetchCategoriesWithHidden : fetchCategories
  return useQueryCache<T[]>(key, fetcher as () => Promise<T[]>, { staleTime: 60_000, version })
}
