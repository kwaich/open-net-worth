// Command-palette relevance scoring. Extracted from CommandPalette.tsx so it can be unit-tested
// directly (a search-ranking guardrail) and shared by the result filter + the default-highlight
// logic. Lower score = better match; `null` = no match at all.
//
// Two tiers, ordered so that ANY contiguous match outranks ANY scattered subsequence:
//
//   Tier 1 — the query appears as a CONTIGUOUS substring of the keywords. For a single
//            keyword string the best occurrence is chosen: whole-word/exact (a ticker like
//            "PHAU") beats a word-start prefix beats a mid-word infix, then earlier position,
//            then shorter haystack. ACROSS different results the kind weights and position are
//            both in play, so an early prefix in a short string can intentionally outrank a
//            whole-word hit buried deep in a long one (position is a real quality signal).
//   Tier 2 — the query is only a SCATTERED subsequence (the legacy greedy scorer). Offset by
//            SCATTER_BASE so it ALWAYS sorts below every Tier-1 match. `null` when the query
//            isn't even a subsequence.
//
// Why: this fixes "search a ticker, get the wrong asset". Typing "phau" used to rank the
// "Physical Gold" account first because greedy subsequence matching grabbed p-h from
// "Ph·ysical" and the account is a short string — while the real PHAU holding scored WORSE
// (its keywords are longer and p-h locked onto "Physical" in the name, not the "PHAU" token).
// Now the exact "PHAU" token is a whole-word Tier-1 hit and wins; the scattered noise
// (Gemini/Coinbase matched p-h-a-u across "cry·P·to exc·H·A·nge acco·U·nt") falls below.
// Mid-word infix search still works ("vidia" → N·vidia) because it's a Tier-1 substring.

const WORD_BOUNDARY = /[\s\-_/.]/

// Larger than any achievable Tier-1 score for realistic keyword strings (kind ≤ 30 +
// position*0.5 + length*0.02), so a contiguous match can never lose to a scattered one.
const SCATTER_BASE = 100000

// Whether a score (as returned by fuzzyScore) came from the Tier-2 scattered fallback
// rather than a Tier-1 contiguous match. Exported so callers (cmdkAssemble.ts) can gate on
// tier without duplicating the SCATTER_BASE constant.
export function isScatterScore(score: number): boolean {
  return score >= SCATTER_BASE
}

// Tier-1 "kind" weights: a whole-word/exact match beats a word-start prefix beats a mid-word
// infix. The gaps are wide so kind dominates position/length tiebreakers within the tier.
const KIND_WHOLE_WORD = 0
const KIND_PREFIX = 10
const KIND_INFIX = 30

export function fuzzyScore(query: string, text: string): number | null {
  const q = query.toLowerCase().trim()
  if (!q) return 0
  const t = text.toLowerCase()

  // Tier 1 — best contiguous occurrence. Scan every occurrence and keep the best (a later
  // whole-word hit can beat an earlier infix one, e.g. "phau" in "alphau… phau").
  let best: number | null = null
  for (let i = t.indexOf(q); i !== -1; i = t.indexOf(q, i + 1)) {
    const startOk = i === 0 || WORD_BOUNDARY.test(t.charAt(i - 1))
    const afterChar = t.charAt(i + q.length) // '' just past the end of the string
    const endOk = afterChar === '' || WORD_BOUNDARY.test(afterChar)
    const kind = startOk && endOk ? KIND_WHOLE_WORD : startOk ? KIND_PREFIX : KIND_INFIX
    const s = kind + i * 0.5 + t.length * 0.02
    if (best === null || s < best) best = s
  }
  if (best !== null) return best

  // Tier 2 — scattered subsequence (the legacy greedy scorer), pushed below all Tier-1 matches.
  let ti = 0
  let score = 0
  let lastMatch = -2
  for (let qi = 0; qi < q.length; qi++) {
    const ch = q.charAt(qi)
    const found = t.indexOf(ch, ti)
    if (found === -1) return null
    // Gap penalty (non-contiguous), softened when the match lands on a word boundary.
    const gap = found - ti
    const boundary = found === 0 || WORD_BOUNDARY.test(t.charAt(found - 1))
    score += gap + (found === lastMatch + 1 ? 0 : boundary ? 1 : 4)
    lastMatch = found
    ti = found + 1
  }
  return SCATTER_BASE + score + t.length * 0.05
}
