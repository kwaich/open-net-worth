/**
 * mergeRows.ts — Framework-free mention row merge / dedupe / filter logic.
 *
 * Pure functions operating on MentionRow arrays. No React, no DOM, no ONW
 * domain imports. Extracted from useMentionables so the core logic is
 * framework-free and extraction-ready (§14.8 R-EX1 / R-EX7).
 *
 * Extraction-readiness: this module has NO ONW-domain imports
 * (@/api, @/state, @/hooks). Only lucius/ core modules are imported.
 */

import type { MentionRow } from './mentionRow'
import { matchEntity } from './matchEntity'

/**
 * Deduplicate rows: primary rows first, secondary appended when not already seen.
 * When a secondary row is a duplicate (same type:value key), its sublabel wins
 * over the primary's if it is non-empty and different — the backend sublabel is
 * authoritative (base-currency amounts, resolved payee names).
 */
export function dedupeRows(primary: MentionRow[], secondary: MentionRow[]): MentionRow[] {
  const seen = new Set<string>()
  const result: MentionRow[] = []
  for (const row of primary) {
    const key = `${row.type}:${row.value}`
    if (seen.has(key)) continue
    seen.add(key)
    result.push(row)
  }
  for (const row of secondary) {
    const key = `${row.type}:${row.value}`
    if (seen.has(key)) {
      // Enrich the local row with the backend sublabel. The backend row's sublabel is
      // authoritative (base-currency amounts, resolved payee names) and always wins.
      const idx = result.findIndex((r) => `${r.type}:${r.value}` === key)
      const existing = idx !== -1 ? result[idx] : undefined
      if (existing && row.sublabel && existing.sublabel !== row.sublabel) {
        // Preserve onScreen: local row's flag wins; backend row never strips it.
        result[idx] = { ...existing, sublabel: row.sublabel } as MentionRow
      }
      continue
    }
    seen.add(key)
    result.push(row)
  }
  return result
}

/**
 * Filter a row list by a query string.
 *
 * First checks data-entity-search (DOM-only broad search text) as a fast
 * literal path, then falls through to matchEntity. Returns the filtered rows
 * annotated with their match hint when present.
 */
export function filterRows(rows: MentionRow[], query: string): MentionRow[] {
  if (!query) return rows
  const lq = query.toLowerCase()
  return rows.flatMap((r) => {
    // data-entity-search: a broad search string set on DOM-harvested rows only.
    if (r.search != null) {
      if (r.search.toLowerCase().includes(lq)) return [r]
    }
    const { ok, hint } = matchEntity(r, query)
    if (!ok) return []
    return [hint ? { ...r, hint } : r]
  })
}
