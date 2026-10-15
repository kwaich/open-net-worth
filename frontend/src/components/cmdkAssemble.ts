// Pure assembly logic for the command palette: score → tier-gate (scatter suppression) →
// group → quality-aware order. Extracted from CommandPalette.tsx so this can be unit-tested
// directly against realistic keyword fixtures (I8 — the scatter-noise-outranks-a-real-hit bug:
// query "iren" surfaced Palantir/Redwire/Automations/etc — all Tier-2 scattered matches — while
// the real "IREN" ticker, which only ever arrives via the server-ranked Markets group, was
// appended unconditionally at the END of the group list and so rendered below all the noise).
//
// Three rules on top of the raw per-row `fuzzyScore` (see cmdkScore.ts):
//
//   1. Scatter suppression — a Tier-2 (scattered subsequence) local match is noise ONLY WHEN
//      something better exists: a Tier-1 (contiguous) hit anywhere, among local rows OR among
//      the current Markets results (scored locally here with the same `fuzzyScore` over their
//      keywords, for this gating decision ONLY — Markets rows are never filtered/reordered by
//      this module; they stay exactly as the server ranked them). If nothing contiguous matches
//      anywhere, scatter is kept as the original typo-tolerant fallback.
//   2. Quality-aware group order — each group is placed by its best (lowest) member score.
//      Local groups: the minimum fuzzyScore among their (gated) members. Markets: the best
//      local fuzzyScore across its rows, so an exact ticker hit like "iren" can promote Markets
//      to the front; a Markets group whose rows don't even fuzzy-match locally scores Infinity
//      ("no signal") and falls back to its position in `fallbackGroupOrder` (its current
//      end-of-list slot), never getting artificially promoted OR demoted by that non-match.
//   3. Holdings beats Markets, always (owner report 2026-08-11, "btc" search) — when both
//      groups are present, Holdings sorts first regardless of either group's raw score. A
//      Markets row can win rule 2's text-match contest on a technicality (a listing literally
//      named "BTC Digital Ltd." out-prefix-matches "Bitcoin"), but what you already OWN is
//      always the more relevant answer than a listing you might add. This is the one
//      group-pair exception to rule 2 — every other pairing stays purely score-based.
//
// Ties (including the trivial all-scores-equal case of an empty query, where every row scores
// 0) fall back to `fallbackGroupOrder`'s position — so a no-query call reproduces
// `fallbackGroupOrder.filter(hasRows)` byte-identically.

import { fuzzyScore, isScatterScore } from './cmdkScore'

export interface AssembleRow {
  id: string
  group: string
  keywords: string
}

export interface AssembleGroup {
  group: string
  ids: string[]
}

const MAX_RESULTS = 40

function scoreRows(query: string, rows: AssembleRow[]): { row: AssembleRow; score: number }[] {
  const scored: { row: AssembleRow; score: number }[] = []
  for (const row of rows) {
    const s = fuzzyScore(query, row.keywords)
    if (s != null) scored.push({ row, score: s })
  }
  return scored
}

/**
 * Score, tier-gate, rank and cap the local rows for a query.
 *
 * `marketRows` are scored the same way but ONLY consulted to decide whether a Tier-1 hit
 * exists anywhere — they are never filtered, reordered, or returned by this function.
 *
 * Empty query: `fuzzyScore` gives every row a score of 0 (never scattered), so this reduces
 * to source order capped at `MAX_RESULTS` — byte-identical to the prior no-query behavior.
 */
export function gateAndRankLocalIds(
  query: string,
  rows: AssembleRow[],
  marketRows: AssembleRow[],
): string[] {
  const scored = scoreRows(query, rows)
  scored.sort((a, b) => a.score - b.score)

  const marketScores = scoreRows(query, marketRows).map((x) => x.score)
  const hasTier1 =
    scored.some((x) => !isScatterScore(x.score)) || marketScores.some((s) => !isScatterScore(s))

  const gated = hasTier1 ? scored.filter((x) => !isScatterScore(x.score)) : scored
  return gated.slice(0, MAX_RESULTS).map((x) => x.row.id)
}

/**
 * Group the already-gated/ranked local rows (the `gateAndRankLocalIds` output, resolved back
 * to rows) together with the Markets rows (server-ranked; a symbol you already hold is NOT
 * deduped out of Markets — see rule 3), and order the resulting groups by quality (see rule 2
 * above).
 *
 * `localRows` keep their given (already-ranked) relative order within their group; `marketRows`
 * keep their given (server) order verbatim — this function only decides group PLACEMENT.
 */
export function orderGroups(
  query: string,
  localRows: AssembleRow[],
  marketRows: AssembleRow[],
  fallbackGroupOrder: string[],
): AssembleGroup[] {
  const localGroupIds = new Map<string, string[]>()
  const localGroupScore = new Map<string, number>()
  for (const row of localRows) {
    const score = fuzzyScore(query, row.keywords) ?? Infinity
    const ids = localGroupIds.get(row.group)
    if (ids) ids.push(row.id)
    else localGroupIds.set(row.group, [row.id])
    const best = localGroupScore.get(row.group)
    if (best === undefined || score < best) localGroupScore.set(row.group, score)
  }

  const hasMarkets = marketRows.length > 0
  const marketBestScore = hasMarkets
    ? Math.min(...marketRows.map((row) => fuzzyScore(query, row.keywords) ?? Infinity))
    : Infinity

  const groupScore = (g: string): number =>
    g === 'Markets' ? marketBestScore : (localGroupScore.get(g) ?? Infinity)

  const presentGroups = fallbackGroupOrder.filter(
    (g) => localGroupIds.has(g) || (g === 'Markets' && hasMarkets),
  )
  const orderedGroupNames = presentGroups
    .map((g, i) => ({ g, score: groupScore(g), i }))
    .sort((a, b) => {
      // Holdings always outranks Markets when both are present — what you already OWN beats
      // a listing you might add, regardless of which one's raw text match scores "better"
      // (a Markets row literally named "BTC Digital Ltd." can out-prefix-match "Bitcoin").
      if (a.g === 'Holdings' && b.g === 'Markets') return -1
      if (a.g === 'Markets' && b.g === 'Holdings') return 1
      return a.score - b.score || a.i - b.i
    })
    .map((x) => x.g)

  return orderedGroupNames.map((g) => ({
    group: g,
    ids: g === 'Markets' ? marketRows.map((r) => r.id) : (localGroupIds.get(g) ?? []),
  }))
}
