import type { BookRow } from './useInvestmentsBook'

// The key Intel stories are indexed by: the position's ticker (what stories carry), falling
// back to the book symbol. Upper-cased, matching useIntelStoryIndex.
export function storyKey(r: BookRow): string {
  return (r._raw?.ticker || r.sym || '').toUpperCase()
}
