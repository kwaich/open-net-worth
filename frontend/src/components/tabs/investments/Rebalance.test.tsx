// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen } from '@testing-library/react'
import { Rebalance } from './Rebalance'
import { fBase } from '../../../utils/format'

afterEach(cleanup)

describe('Rebalance — MATH-38 trade sizing uses the server figure, never rounded percents', () => {
  it('sizes the trade from the server trade_base/drift_pp, not from the rounded cur/tgt display percents', () => {
    // Book 1,000,000, current 32.6%, target 29.4% → the true trade is -32,000 (the server's own
    // `trade_base`). Sizing off the rounded 33%/29% display percents instead gives -40,000 —
    // that used to be the bug.
    render(
      <Rebalance
        thresholdPp={3}
        alloc={[{ cls: 'equity', cur: 33, tgt: 29, driftPp: 3.2, tradeBase: -32_000 }]}
      />,
    )

    expect(screen.getByText(fBase(32_000), { exact: false })).toBeTruthy()
    expect(screen.queryByText(fBase(40_000), { exact: false })).toBeNull()
  })

  it('filters trades by the server threshold_pp, never a hardcoded number', () => {
    // drift_pp 4 sits below a 5pp threshold — no trade — even though an old hardcoded `>= 3`
    // threshold would have flagged it.
    const { container } = render(
      <Rebalance
        thresholdPp={5}
        alloc={[{ cls: 'equity', cur: 54, tgt: 50, driftPp: 4, tradeBase: 40_000 }]}
      />,
    )
    expect(container.textContent).toContain('on target')
    expect(container.textContent).not.toMatch(/TRIM|BUY/)
  })

  it('flags a trade once drift reaches a lower server threshold', () => {
    const { container } = render(
      <Rebalance
        thresholdPp={3}
        alloc={[{ cls: 'equity', cur: 54, tgt: 50, driftPp: 4, tradeBase: 40_000 }]}
      />,
    )
    expect(container.textContent).toMatch(/TRIM|BUY/)
  })
})
