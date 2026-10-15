import { describe, it, expect } from 'vitest'
import { gateAndRankLocalIds, orderGroups, type AssembleRow } from './cmdkAssemble'

// Fixture rows in the real keyword formats CommandPalette.tsx builds (see cmdkScore.test.ts
// for the same convention). This is the owner's exact "iren" repro: every local row here is
// ONLY a Tier-2 scattered subsequence match for "iren" (measured scores ~100025–100047, all
// >= SCATTER_BASE) — the keywords string never contains "iren" contiguously.
const GROUP_ORDER = [
  'Quick actions',
  'Tabs',
  'Accounts',
  'Holdings',
  'Categories',
  'Markets',
  'Ask Lucius',
]

const LOCAL_ROWS: AssembleRow[] = [
  {
    id: 'hold-pltr',
    group: 'Holdings',
    keywords: 'Palantir Technologies Inc. PLTR PLTR equity holding instrument',
  },
  {
    id: 'hold-rdw',
    group: 'Holdings',
    keywords: 'Redwire Corporation RDW RDW equity holding instrument',
  },
  { id: 'cat-split', group: 'Categories', keywords: 'Split category spending' },
  {
    id: 'tab-automations',
    group: 'Tabs',
    keywords: 'Automations Rules and scheduled tasks tab go to',
  },
  { id: 'tab-inbox', group: 'Tabs', keywords: 'Inbox Everything needing attention tab go to' },
  {
    id: 'acct-vantage-usd',
    group: 'Accounts',
    keywords: 'Vantage International Brokerage (USD) brokerage USD account',
  },
  { id: 'acct-meridian', group: 'Accounts', keywords: 'Meridian Rewards+ credit_card USD account' },
]

// The genuine, exact-ticker Markets hit — a Tier-1 whole-word match (score 0.48) that used
// to bypass local scoring entirely for placement and land at the end of the group list.
const IREN_MARKET_ROW: AssembleRow = {
  id: 'mkt-IREN',
  group: 'Markets',
  keywords: 'IREN LIMITED IREN equity',
}

describe('gateAndRankLocalIds — scatter suppression', () => {
  it('"iren" with the IREN market row present: drops every scattered local row (none are Tier-1)', () => {
    const ids = gateAndRankLocalIds('iren', LOCAL_ROWS, [IREN_MARKET_ROW])
    expect(ids).toEqual([])
  })

  it('"iren" with NO market results: scatter fallback still returns the local rows (nothing better exists)', () => {
    const ids = gateAndRankLocalIds('iren', LOCAL_ROWS, [])
    expect(ids.length).toBe(LOCAL_ROWS.length)
    // Still ranked best (lowest score) first, per the pre-existing scatter ordering.
    expect(ids[0]).toBe('hold-pltr')
  })

  it('"pltr" Tier-1-matches the Palantir holding: it is kept, and other scatter-only rows are dropped', () => {
    const ids = gateAndRankLocalIds('pltr', LOCAL_ROWS, [])
    expect(ids).toContain('hold-pltr')
    expect(ids).toEqual(['hold-pltr'])
  })

  it('empty query: byte-identical to source order, no gating applied', () => {
    const ids = gateAndRankLocalIds('', LOCAL_ROWS, [])
    expect(ids).toEqual(LOCAL_ROWS.map((r) => r.id))
  })
})

describe('orderGroups — quality-aware group placement', () => {
  it('"iren": Markets group is FIRST (its exact ticker hit outranks any local group)', () => {
    const localIds = gateAndRankLocalIds('iren', LOCAL_ROWS, [IREN_MARKET_ROW])
    const localRows = localIds.map((id) => LOCAL_ROWS.find((r) => r.id === id)!)
    const groups = orderGroups('iren', localRows, [IREN_MARKET_ROW], GROUP_ORDER)

    expect(groups.length).toBe(1) // every local row was suppressed as scatter noise
    expect(groups[0]!.group).toBe('Markets')
    expect(groups[0]!.ids).toEqual(['mkt-IREN'])

    const flat = groups.flatMap((g) => g.ids)
    expect(flat[0]).toBe('mkt-IREN')
  })

  it('"iren" with no Markets rows: falls back to the scattered local rows, grouped/ordered as before', () => {
    const localIds = gateAndRankLocalIds('iren', LOCAL_ROWS, [])
    const localRows = localIds.map((id) => LOCAL_ROWS.find((r) => r.id === id)!)
    const groups = orderGroups('iren', localRows, [], GROUP_ORDER)

    expect(groups.some((g) => g.group === 'Markets')).toBe(false)
    const flat = groups.flatMap((g) => g.ids)
    expect(flat).toEqual(localIds)
  })

  it('"pltr": Holdings is placed by its match quality, and only the Tier-1 row survives', () => {
    const localIds = gateAndRankLocalIds('pltr', LOCAL_ROWS, [])
    const localRows = localIds.map((id) => LOCAL_ROWS.find((r) => r.id === id)!)
    const groups = orderGroups('pltr', localRows, [], GROUP_ORDER)

    expect(groups).toEqual([{ group: 'Holdings', ids: ['hold-pltr'] }])
  })

  it('a Markets row whose keywords do not fuzzy-match locally keeps its fallback (end) slot', () => {
    // A Markets row utterly unrelated to the query — no local fuzzy signal at all — must not
    // be promoted; it stays at its GROUP_ORDER position (after every present local group).
    const unrelatedMarketRow: AssembleRow = {
      id: 'mkt-ZZZ',
      group: 'Markets',
      keywords: 'Zzz Corp ZZZ equity',
    }
    const localIds = gateAndRankLocalIds('pltr', LOCAL_ROWS, [unrelatedMarketRow])
    const localRows = localIds.map((id) => LOCAL_ROWS.find((r) => r.id === id)!)
    const groups = orderGroups('pltr', localRows, [unrelatedMarketRow], GROUP_ORDER)

    expect(groups.map((g) => g.group)).toEqual(['Holdings', 'Markets'])
  })

  it('"btc": Holdings beats Markets even when a Markets row text-matches "better" (owner report, 2026-08-11)', () => {
    // Mirrors the real bug: "Bitcoin" (Holdings) only Tier-1-matches mid-string ("...BTC...");
    // "BTC Digital Ltd." (Markets) matches right at position 0 — a strictly BETTER raw
    // fuzzyScore — yet Holdings must still sort first.
    const btcLocalRows: AssembleRow[] = [
      {
        id: 'hold-btc',
        group: 'Holdings',
        keywords: 'Bitcoin BTC BTC crypto holding instrument',
      },
    ]
    const btcMarketRow: AssembleRow = {
      id: 'mkt-yahoo-BTCT',
      group: 'Markets',
      keywords: 'BTC Digital Ltd. BTCT equity',
    }
    const groups = orderGroups('btc', btcLocalRows, [btcMarketRow], GROUP_ORDER)

    expect(groups.map((g) => g.group)).toEqual(['Holdings', 'Markets'])
  })

  it('empty query: group order is byte-identical to GROUP_ORDER filtered by presence', () => {
    const localIds = gateAndRankLocalIds('', LOCAL_ROWS, [])
    const localRows = localIds.map((id) => LOCAL_ROWS.find((r) => r.id === id)!)
    const groups = orderGroups('', localRows, [], GROUP_ORDER)

    expect(groups.map((g) => g.group)).toEqual(['Tabs', 'Accounts', 'Holdings', 'Categories'])
    // Source order preserved within each group.
    expect(groups.find((g) => g.group === 'Tabs')?.ids).toEqual(['tab-automations', 'tab-inbox'])
    expect(groups.find((g) => g.group === 'Accounts')?.ids).toEqual([
      'acct-vantage-usd',
      'acct-meridian',
    ])
  })
})
