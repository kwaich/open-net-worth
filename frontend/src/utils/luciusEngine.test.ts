import { describe, it, expect } from 'vitest'
import type { LuciusAsset, LuciusContext, LuciusReviewItem } from './luciusEngine'
import {
  luciusSuggestAlerts,
  luciusDraftTopicInstruction,
  luciusReviewList,
  lxRound,
  lxFmt,
  lxDist,
} from './luciusEngine'

// ---- shared fixtures ----

const ctxFull: LuciusContext = {
  holdingsBySymbol: {
    AAPL: { symbol: 'AAPL', cls: 'equity', qty: 100, weight: 18, plPct: 40 },
    BTC: { symbol: 'BTC', cls: 'crypto', qty: 0.5, weight: 22, plPct: 60 },
    TSLA: { symbol: 'TSLA', cls: 'equity', qty: 20, weight: 5, plPct: -8 },
  },
  targets: { equity: 15, crypto: 10, commodity: 8, bond: 5 },
  classLabels: { equity: 'Equities', crypto: 'Crypto', commodity: 'Commodities', bond: 'Bonds' },
}

const emptyCtx: LuciusContext = {
  holdingsBySymbol: {},
  targets: { equity: 15, crypto: 10 },
  classLabels: { equity: 'Equities', crypto: 'Crypto' },
}

// ---- lxRound ----
describe('lxRound', () => {
  it('rounds large numbers to 500s', () => expect(lxRound(12345)).toBe(12500))
  it('rounds 1000-9999 to 100s', () => expect(lxRound(1234)).toBe(1200))
  it('rounds 100-999 to 5s', () => expect(lxRound(123)).toBe(125))
  it('rounds 10-99 to halves', () => expect(lxRound(12.3)).toBe(12.5))
  it('rounds < 10 to 2dp', () => expect(lxRound(1.234)).toBe(1.23))
})

// ---- lxFmt ----
describe('lxFmt', () => {
  it('adds thousands separator above 1000', () => expect(lxFmt(1234)).toBe('1,234'))
  it('leaves numbers below 1000 as strings', () => expect(lxFmt(999)).toBe('999'))
})

// ---- lxDist ----
describe('lxDist', () => {
  it('returns rounded integer percent distance', () => {
    expect(lxDist(110, 100)).toBe(10)
    expect(lxDist(90, 100)).toBe(10)
  })
})

// ---- luciusSuggestAlerts ----
describe('luciusSuggestAlerts', () => {
  describe('owned overweight big-winner (AAPL: weight=18%, target=15%, plPct=40%)', () => {
    const asset: LuciusAsset = { symbol: 'AAPL', ccy: 'USD', price: 200, cls: 'equity' }

    it('produces a trim-above suggestion', () => {
      const suggestions = luciusSuggestAlerts(asset, ctxFull)
      const trimAbove = suggestions.find((s) => s.op === 'above')
      expect(trimAbove).toBeDefined()
      // why should mention overweight / target
      expect(trimAbove!.why).toMatch(/over your 15% equities target/i)
      // note should mention trimming and share count
      expect(trimAbove!.note).toMatch(/trim/i)
      expect(trimAbove!.note).toMatch(/shares/)
    })

    it('produces a protect-gain below suggestion (plPct > 25)', () => {
      const suggestions = luciusSuggestAlerts(asset, ctxFull)
      const protect = suggestions.find((s) => s.op === 'below')
      expect(protect).toBeDefined()
      expect(protect!.why).toMatch(/up 40%/)
      expect(protect!.note).toMatch(/trailing stop|trimming/)
    })
  })

  describe('not-owned asset produces entry + breakout', () => {
    const asset: LuciusAsset = { symbol: 'NVDA', ccy: 'USD', price: 1000 }

    it('produces an entry-below suggestion', () => {
      const suggestions = luciusSuggestAlerts(asset, emptyCtx)
      const entry = suggestions.find((s) => s.op === 'below')
      expect(entry).toBeDefined()
      expect(entry!.why).toMatch(/You don't own NVDA/)
      expect(entry!.note).toMatch(/buying opportunity/)
    })

    it('produces a breakout-above suggestion', () => {
      const suggestions = luciusSuggestAlerts(asset, emptyCtx)
      const breakout = suggestions.find((s) => s.op === 'above')
      expect(breakout).toBeDefined()
      expect(breakout!.why).toMatch(/confirm momentum/)
      expect(breakout!.note).toMatch(/starter position/)
    })
  })

  describe('owned losing position (TSLA: plPct=-8%)', () => {
    const asset: LuciusAsset = { symbol: 'TSLA', ccy: 'USD', price: 200, cls: 'equity' }

    it('produces a decision-level below suggestion (~10% below cost)', () => {
      const suggestions = luciusSuggestAlerts(asset, ctxFull)
      const decision = suggestions.find((s) => s.op === 'below')
      expect(decision).toBeDefined()
      expect(decision!.why).toMatch(/down 8%/)
      expect(decision!.why).toMatch(/10% below your cost/)
      expect(decision!.note).toMatch(/averaging down|cutting/)
    })
  })

  describe('crypto (BTC: overweight 22% vs 10% target, plPct=60%)', () => {
    const asset: LuciusAsset = { symbol: 'BTC', ccy: 'USD', price: 60000, cls: 'crypto' }

    it('produces a stretch-above suggestion in addition to the standard ones', () => {
      const suggestions = luciusSuggestAlerts(asset, ctxFull)
      const aboves = suggestions.filter((s) => s.op === 'above')
      // should have trim + stretch for owned overweight crypto
      expect(aboves.length).toBeGreaterThanOrEqual(2)
      const stretch = aboves.find((s) => s.note.includes('rebalance crypto'))
      expect(stretch).toBeDefined()
      expect(stretch!.why).toMatch(/crypto can move fast/)
    })
  })

  describe('alert proximity filter (within 2.5% filtered out)', () => {
    it('filters suggestions that are within 2.5% of an existing alert', () => {
      // NVDA at 1000 not-owned — below band level = lxRound(1000 * 0.88) = 880
      // Add an existing alert right at 882 (within 2.5% of 880)
      const asset: LuciusAsset = {
        symbol: 'NVDA',
        ccy: 'USD',
        price: 1000,
        alerts: [{ op: 'below', value: 882 }],
      }
      const suggestions = luciusSuggestAlerts(asset, emptyCtx)
      // The below 880 suggestion should be filtered out (882 is within 2.5% of 880)
      const below = suggestions.find((s) => s.op === 'below')
      expect(below).toBeUndefined()
    })

    it('keeps suggestions far enough from existing alerts', () => {
      const asset: LuciusAsset = {
        symbol: 'NVDA',
        ccy: 'USD',
        price: 1000,
        alerts: [{ op: 'below', value: 700 }], // far from the ~880 band level
      }
      const suggestions = luciusSuggestAlerts(asset, emptyCtx)
      const below = suggestions.find((s) => s.op === 'below')
      expect(below).toBeDefined()
    })
  })
})

// ---- luciusDraftTopicInstruction ----
describe('luciusDraftTopicInstruction', () => {
  it('returns rates-specific text for "Rate cuts"', () => {
    const result = luciusDraftTopicInstruction('Rate cuts')
    expect(result).toContain('Track developments on Rate cuts.')
    expect(result).toMatch(/rate-path shifts|bond sleeve/)
  })

  it('returns geopolitics text for "Iran war"', () => {
    const result = luciusDraftTopicInstruction('Iran war')
    expect(result).toContain('Track developments on Iran war.')
    expect(result).toMatch(/escalation|de-escalation/)
    expect(result).toMatch(/safe-haven gold/)
  })

  it('returns AI/chip text for "NVIDIA chips"', () => {
    const result = luciusDraftTopicInstruction('NVIDIA chips')
    expect(result).toMatch(/NVDA, MSFT/)
  })

  it('returns crypto text for "Bitcoin ETF flows"', () => {
    const result = luciusDraftTopicInstruction('Bitcoin ETF flows')
    expect(result).toMatch(/BTC and ETH/)
  })

  it('returns oil/energy text for "OPEC production"', () => {
    const result = luciusDraftTopicInstruction('OPEC production')
    expect(result).toMatch(/supply or price shocks/)
  })

  it('returns generic text for an unrecognised topic', () => {
    const result = luciusDraftTopicInstruction('Random topic')
    expect(result).toContain('Track developments on Random topic.')
    expect(result).toMatch(/risk-on\/risk-off/)
  })

  it('ends every result with the summarise sentence', () => {
    const result = luciusDraftTopicInstruction('FOMC')
    expect(result).toMatch(/Summarise what changed/)
  })
})

// ---- luciusReviewList ----
describe('luciusReviewList', () => {
  it('flags a ticker with no alerts as a gap', () => {
    const items: LuciusReviewItem[] = [{ _id: 'a1', kind: 'ticker', symbol: 'AAPL', alerts: [] }]
    const result = luciusReviewList({ name: 'Tech', items }, ctxFull)
    const finding = result.findings.find((f) => f.key === 'a1')
    expect(finding).toBeDefined()
    expect(finding!.sev).toBe('gap')
    expect(finding!.title).toMatch(/AAPL has no price alerts/)
    expect(finding!.cta).toBe('Suggest alerts')
  })

  it('flags a too-short topic note as a gap', () => {
    const items: LuciusReviewItem[] = [
      { _id: 't1', kind: 'topic', label: 'AI', note: 'Short note' },
    ]
    const result = luciusReviewList({ name: 'Tech', items }, emptyCtx)
    const finding = result.findings.find((f) => f.key === 't1')
    expect(finding).toBeDefined()
    expect(finding!.sev).toBe('gap')
    expect(finding!.kind).toBe('topic')
    expect(finding!.cta).toBe('Draft instructions')
  })

  it('flags an overweight holding (weight > target+3) as risk', () => {
    // AAPL weight=18% > target 15%+3 = 18% — exactly at threshold, should NOT trigger (> not >=)
    // Use BTC weight=22% > target 10%+3 = 13% — should trigger
    const items: LuciusReviewItem[] = [
      { _id: 'b1', kind: 'ticker', symbol: 'BTC', alerts: [{ op: 'above', value: 70000 }] },
    ]
    const result = luciusReviewList({ name: 'Portfolio', items }, ctxFull)
    const finding = result.findings.find((f) => f.key === 'b1:ow')
    expect(finding).toBeDefined()
    expect(finding!.sev).toBe('risk')
    expect(finding!.title).toMatch(/BTC is overweight/)
    expect(finding!.cta).toBe('Add alert')
  })

  it('flags a muted list with info finding + noCta', () => {
    const result = luciusReviewList({ name: 'Muted', muted: true, items: [] }, emptyCtx)
    const muted = result.findings.find((f) => f.key === 'muted')
    expect(muted).toBeDefined()
    expect(muted!.sev).toBe('info')
    expect(muted!.noCta).toBe(true)
  })

  it('produces a clean headline when nothing is actionable', () => {
    // Ticker with alerts, topic with a long enough note
    const items: LuciusReviewItem[] = [
      {
        _id: 'a1',
        kind: 'ticker',
        symbol: 'AAPL',
        alerts: [{ op: 'above', value: 250 }],
      },
      {
        _id: 't1',
        kind: 'topic',
        label: 'AI chips',
        note: 'This is a very detailed note about AI chips and their impact on my portfolio and the market',
      },
    ]
    const result = luciusReviewList({ name: 'Tech', items }, emptyCtx)
    expect(result.headline).toMatch(/looks well-covered/)
  })

  it('produces a plural actionable headline when multiple things need fixing', () => {
    const items: LuciusReviewItem[] = [
      { _id: 'a1', kind: 'ticker', symbol: 'AAPL', alerts: [] },
      { _id: 't1', kind: 'topic', label: 'AI', note: 'short' },
    ]
    const result = luciusReviewList({ name: 'Tech', items }, emptyCtx)
    expect(result.headline).toMatch(/2 things to tighten up/)
  })

  it('produces a singular actionable headline for exactly one finding', () => {
    const items: LuciusReviewItem[] = [{ _id: 'a1', kind: 'ticker', symbol: 'NVDA', alerts: [] }]
    const result = luciusReviewList({ name: 'Tech', items }, emptyCtx)
    expect(result.headline).toMatch(/1 thing to tighten up/)
  })

  it('skips muted items in the asset list', () => {
    const items: LuciusReviewItem[] = [
      { _id: 'a1', kind: 'ticker', symbol: 'AAPL', muted: true, alerts: [] },
    ]
    const result = luciusReviewList({ name: 'Tech', items }, emptyCtx)
    // the muted ticker should not generate a gap finding
    const aaplFinding = result.findings.find((f) => f.key === 'a1')
    expect(aaplFinding).toBeUndefined()
  })

  it('sets itemId from _id when present', () => {
    const items: LuciusReviewItem[] = [
      { _id: 'abc123', kind: 'ticker', symbol: 'TSLA', alerts: [] },
    ]
    const result = luciusReviewList({ name: 'Growth', items }, emptyCtx)
    const finding = result.findings[0]
    expect(finding).toBeDefined()
    expect(finding!.itemId).toBe('abc123')
  })
})
