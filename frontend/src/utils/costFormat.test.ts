import { describe, it, expect } from 'vitest'
import { fUsd, fTokens, labelSource, sortByCost, totalTokens, sumSessionCost } from './costFormat'
import type { CostTotals } from './costFormat'

const makeTotals = (cost_usd: number, extra: Partial<CostTotals> = {}): CostTotals => ({
  turns: 1,
  input_tokens: 1000,
  cache_creation_tokens: 0,
  cache_read_tokens: 0,
  output_tokens: 500,
  cost_usd,
  ...extra,
})

describe('fUsd', () => {
  it('formats zero', () => {
    expect(fUsd(0)).toBe('$0.0000')
  })

  it('formats sub-cent amounts with 4 decimal places', () => {
    expect(fUsd(0.0023)).toBe('$0.0023')
  })

  it('formats dollar-range amounts', () => {
    expect(fUsd(1.2345)).toBe('$1.2345')
  })

  it('handles Infinity gracefully', () => {
    expect(fUsd(Infinity)).toBe('$0.0000')
  })

  it('formats larger cost with 4dp', () => {
    expect(fUsd(12.5)).toBe('$12.5000')
  })
})

describe('fTokens', () => {
  it('formats small counts as-is', () => {
    expect(fTokens(0)).toBe('0')
    expect(fTokens(999)).toBe('999')
  })

  it('uses K suffix for thousands', () => {
    expect(fTokens(1_234)).toBe('1.2K')
    expect(fTokens(10_000)).toBe('10.0K')
  })

  it('uses M suffix for millions', () => {
    expect(fTokens(1_234_567)).toBe('1.2M')
  })

  it('handles negative / NaN gracefully', () => {
    expect(fTokens(-1)).toBe('0')
    expect(fTokens(NaN)).toBe('0')
  })
})

describe('labelSource', () => {
  it('labels known sources', () => {
    expect(labelSource('chat')).toBe('Chat')
    expect(labelSource('scheduled')).toBe('Scheduled')
    expect(labelSource('memory')).toBe('Memory')
    expect(labelSource('headless')).toBe('Headless')
  })

  it('extracts agent id', () => {
    expect(labelSource('agent:abc123')).toBe('Agent: abc123')
    expect(labelSource('agent:monitor')).toBe('Agent: monitor')
  })

  it('capitalises unknown sources', () => {
    expect(labelSource('unknown_tool')).toBe('Unknown tool')
    expect(labelSource('batch-run')).toBe('Batch run')
  })

  it('returns Unknown for empty string', () => {
    expect(labelSource('')).toBe('Unknown')
  })
})

describe('sortByCost', () => {
  it('sorts descending by cost_usd', () => {
    const map: Record<string, CostTotals> = {
      cheap: makeTotals(0.001),
      expensive: makeTotals(0.5),
      mid: makeTotals(0.1),
    }
    const result = sortByCost(map)
    expect(result.map((r) => r.key)).toEqual(['expensive', 'mid', 'cheap'])
  })

  it('returns an empty array for an empty map', () => {
    expect(sortByCost({})).toEqual([])
  })
})

describe('sumSessionCost', () => {
  it('returns 0 for an empty conversation', () => {
    expect(sumSessionCost([])).toBe(0)
  })

  it('ignores messages without usage', () => {
    expect(
      sumSessionCost([
        {
          /* user message */
        },
        {},
      ]),
    ).toBe(0)
  })

  it('sums cost_usd across all messages with usage', () => {
    const msgs = [
      {}, // user
      { usage: { cost_usd: 0.002 } },
      {}, // user
      { usage: { cost_usd: 0.015 } },
    ]
    expect(sumSessionCost(msgs)).toBeCloseTo(0.017)
  })

  it('ignores in-progress streaming turns (no usage yet)', () => {
    const msgs = [
      { usage: { cost_usd: 0.01 } },
      {}, // streaming assistant — usage not set yet
    ]
    expect(sumSessionCost(msgs)).toBeCloseTo(0.01)
  })
})

describe('totalTokens', () => {
  it('sums input + cache_creation + output', () => {
    const t = makeTotals(0, {
      input_tokens: 1000,
      cache_creation_tokens: 200,
      cache_read_tokens: 999,
      output_tokens: 300,
    })
    // cache_read excluded
    expect(totalTokens(t)).toBe(1500)
  })

  it('returns 0 for all-zero totals', () => {
    const t = makeTotals(0, {
      input_tokens: 0,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
      output_tokens: 0,
    })
    expect(totalTokens(t)).toBe(0)
  })

  // F3 regression: the DailySparkline tooltip must include cache_creation_tokens
  // (by using totalTokens(d) rather than d.input_tokens + d.output_tokens).
  it('includes cache_creation_tokens — F3 daily tooltip regression', () => {
    const withCache = makeTotals(0, {
      input_tokens: 500,
      cache_creation_tokens: 250,
      cache_read_tokens: 100,
      output_tokens: 200,
    })
    // Without the fix: 500 + 200 = 700; with the fix: 500 + 250 + 200 = 950.
    expect(totalTokens(withCache)).toBe(950)
    // Verify cache_read is still excluded.
    expect(totalTokens(withCache)).not.toBe(500 + 100 + 250 + 200)
  })
})
