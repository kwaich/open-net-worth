import { describe, it, expect } from 'vitest'
import { fuzzyScore } from './cmdkScore'

// Keyword strings exactly as CommandPalette builds them, so the test reflects real ranking:
//   accounts  → `${name} ${type} ${currency} account`
//   holdings  → `${label} ${sym} ${ticker} ${asset_class} holding instrument`
//   tabs      → `${label} ${hint} tab go to`
const KW = {
  phauHolding: 'WisdomTree Physical Gold PHAU PHAU commodity holding instrument',
  nvidiaHolding: 'Nvidia NVDA NVDA equity holding instrument',
  physicalGoldAcct: 'Physical Gold custody usd account',
  physicalSilverAcct: 'Physical Silver custody usd account',
  geminiAcct: 'Gemini crypto exchange usd account',
  coinbaseAcct: 'Coinbase crypto exchange usd account',
  dbsAcct: 'Meridian Bank PayHub ewallet sgd account',
  investmentsTab: 'Investments Stocks, ETFs, crypto, commodities tab go to',
}

/** Sort a labelled set of keyword strings best→worst for a query (mirrors the palette filter). */
function rank(query: string, entries: Record<string, string>): string[] {
  return Object.entries(entries)
    .map(([k, kw]) => ({ k, s: fuzzyScore(query, kw) }))
    .filter((x): x is { k: string; s: number } => x.s != null)
    .sort((a, b) => a.s - b.s)
    .map((x) => x.k)
}

describe('fuzzyScore — basics', () => {
  it('empty query scores 0', () => {
    expect(fuzzyScore('', 'anything')).toBe(0)
    expect(fuzzyScore('   ', 'anything')).toBe(0)
  })

  it('is case-insensitive', () => {
    expect(fuzzyScore('PHAU', KW.phauHolding)).toBe(fuzzyScore('phau', KW.phauHolding))
  })

  it('returns null when the query is not even a subsequence', () => {
    expect(fuzzyScore('xyz', 'Physical Gold custody usd account')).toBeNull()
    expect(fuzzyScore('zzz', KW.geminiAcct)).toBeNull()
  })
})

describe('fuzzyScore — tier ordering (contiguous beats scattered; whole-word beats prefix beats infix)', () => {
  it('whole-word < prefix < infix < scattered', () => {
    const wholeWord = fuzzyScore('ab', 'ab cd')! // "ab" is its own word
    const prefix = fuzzyScore('ab', 'abcd ef')! // "ab" starts the word "abcd"
    const infix = fuzzyScore('bc', 'abcd')! // "bc" is mid-word
    const scattered = fuzzyScore('ad', 'a x d')! // a…d, no "ad" substring
    expect(wholeWord).toBeLessThan(prefix)
    expect(prefix).toBeLessThan(infix)
    expect(infix).toBeLessThan(scattered)
  })

  it('any contiguous substring match beats any scattered subsequence', () => {
    const infix = fuzzyScore('bcd', 'zzzbcdzzz')! // contiguous (mid-word)
    const scattered = fuzzyScore('bcd', 'b z c z d')! // scattered, no "bcd" substring
    expect(infix).toBeLessThan(scattered)
  })

  it('picks the BEST occurrence: a later whole-word hit beats an earlier infix hit', () => {
    // "phau" appears first as an infix ("alphau…") then as a whole word — the whole-word
    // occurrence must win (the scan keeps the lowest-kind occurrence).
    const wholeWordPresent = fuzzyScore('phau', 'alphau-xyz phau')!
    const onlyInfix = fuzzyScore('phau', 'xalphauyz')! // no whole-word occurrence anywhere
    expect(wholeWordPresent).toBeLessThan(10) // scored as whole-word (kind 0), not infix (30)
    expect(wholeWordPresent).toBeLessThan(onlyInfix)
  })
})

describe('fuzzyScore — the PHAU ticker bug (regression)', () => {
  it('ranks the PHAU holding ABOVE the Physical Gold account and all crypto-exchange noise', () => {
    const order = rank('phau', KW)
    expect(order[0]).toBe('phauHolding') // the exact ticker wins, not "Physical Gold"
  })

  it('PHAU holding scores strictly better than the account/exchange noise', () => {
    const holding = fuzzyScore('phau', KW.phauHolding)!
    expect(holding).toBeLessThan(fuzzyScore('phau', KW.physicalGoldAcct)!)
    expect(holding).toBeLessThan(fuzzyScore('phau', KW.geminiAcct)!)
    expect(holding).toBeLessThan(fuzzyScore('phau', KW.coinbaseAcct)!)
    expect(holding).toBeLessThan(fuzzyScore('phau', KW.dbsAcct)!)
  })

  it('the crypto-exchange "phau" matches are only scattered noise (large scores), not contiguous', () => {
    // They still technically match (p-h-a-u smeared across crypto/exchange/account) but must
    // land in the scattered tier, far below the real ticker.
    expect(fuzzyScore('phau', KW.geminiAcct)!).toBeGreaterThan(1000)
    expect(fuzzyScore('phau', KW.coinbaseAcct)!).toBeGreaterThan(1000)
  })
})

describe('fuzzyScore — does NOT regress legitimate searches', () => {
  it('infix / partial-name search still works: "vidia" → Nvidia', () => {
    const s = fuzzyScore('vidia', KW.nvidiaHolding)
    expect(s).not.toBeNull()
    expect(s!).toBeLessThan(1000) // contiguous (infix), not dropped to the scattered tier
  })

  it('exact ticker search lands on the holding: "nvda"', () => {
    expect(rank('nvda', KW)[0]).toBe('nvidiaHolding')
  })

  it('currency search works: "usd" is a whole-word hit on USD accounts', () => {
    const s = fuzzyScore('usd', KW.geminiAcct)
    expect(s).not.toBeNull()
    expect(s!).toBeLessThan(1000)
  })

  it('account-name prefix search works: "gemini"', () => {
    expect(rank('gemini', KW)[0]).toBe('geminiAcct')
  })

  it('"physical" still ranks the Physical accounts/holdings at the top (shared prefix)', () => {
    const order = rank('physical', KW)
    expect(['physicalGoldAcct', 'physicalSilverAcct', 'phauHolding']).toContain(order[0])
  })
})
