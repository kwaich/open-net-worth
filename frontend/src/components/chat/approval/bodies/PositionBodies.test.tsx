// @vitest-environment jsdom
//
// Every position / lot / income approval card, rendered from its fixture through
// ChangePreview: each shows the facts a user approves on, and none uses a legacy class or an
// inline style (docs/APPROVAL_CARDS.md). `lot_delete` renders its body directly until the
// dispatch in CardBody learns it.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { ChangePreview } from '../../ChangePreview'
import { POSITION_FIXTURES } from '../fixtures/positions'
import type { ProposedChange } from '../../../../types/chat'

afterEach(cleanup)

const LEGACY =
  /^(change-diff-table|diff-after|diff-before|change-preview-reason|prompt-|permission-|automation-|code-run-source|realized-|dup-)/

interface Expected {
  lead?: string
  meta?: string
  /** A card's own reason renders last, as one muted note. */
  reason?: string
  facts?: Record<string, string>
  warn?: string[]
  summary?: string
}

const EXPECTED: Record<string, Expected> = {
  'update · quantity and basis edit': {
    lead: 'Change quantity, cost basis',
    reason: 'Reconciling to the November statement',
    facts: { Quantity: '40 → 42.5', 'Cost basis': '$19,800.00 → $21,400.00' },
  },
  'update · currency-only correction': {
    lead: 'Change cost currency',
    reason: 'The basis was paid in Singapore dollars',
    facts: { 'Cost currency': 'USD → SGD' },
  },
  'update · moves to a new account': {
    lead: 'Change account',
    facts: { Account: 'Meridian Wealth → Northgate Brokerage (new account)' },
  },
  'add · with cost basis': {
    lead: 'Add 10 VOO',
    meta: 'Vanguard S&P 500 ETF · equity · Oct 9, 2026 · $4.95 fees',
    facts: { 'Cost basis': '$6,040.00' },
  },
  'add · no cost basis': {
    lead: 'Add 25 VWRA',
    meta: 'Vanguard FTSE All-World UCITS ETF · equity',
    facts: { 'Cost basis': 'unknown' },
  },
  'add · foreign cost currency': {
    lead: 'Add 0.04 BTC',
    meta: 'Bitcoin · crypto',
    facts: { 'Cost basis': 'S$80,000.00' },
  },
  'remove · position': {
    lead: 'Remove Vanguard S&P 500 ETF40.1867 units',
    reason: 'Closed at the broker',
  },
  'lot_acquire · buy with fees': {
    lead: 'Buy 10 VOO @ $604.00$6,040.00',
    meta: 'Oct 9, 2026 · $4.95 fees',
    facts: { Cash: 'debited from Meridian Cash' },
  },
  'lot_acquire · buy with no funding account': {
    lead: 'Buy 2.5 VOO @ $598.20$1,495.50',
    meta: 'Sep 18, 2026',
    facts: { Cash: 'not booked' },
    warn: ['Cash'],
  },
  'lot_acquire · transfer_in': {
    lead: 'Transfer in 3 VOO @ $452.65$1,357.95',
    meta: 'May 3, 2024',
    facts: {},
  },
  'lot_acquire · unknown cost basis': {
    lead: 'Transfer in 1.25 VOO',
    meta: 'Feb 14, 2023',
    facts: { 'Cost basis': 'unknown' },
  },
  'lot_dispose · sale, no cash account': {
    lead: 'Sell 3 @ $615.00$1,845.00',
    meta: 'Oct 11, 2026 · FIFO',
    facts: { 'Realized gain': '+$510.80', Cash: 'not booked' },
    warn: ['Cash'],
    summary: '2 lots · all long-term',
  },
  'lot_dispose · sale with deposit account and fees': {
    lead: 'Sell 3 @ $615.00$1,845.00',
    meta: 'Oct 11, 2026 · FIFO · $4.95 fees',
    facts: { 'Realized gain': '+$482.10', Cash: 'Brokerage Cash' },
    summary: '1 lot · long-term',
  },
  'lot_dispose · mixed long and short lots, a loss': {
    lead: 'Sell 3 @ $430.00$1,290.00',
    meta: 'Oct 11, 2026 · FIFO',
    facts: { 'Realized gain': '-$365.05', Cash: 'not booked' },
    warn: ['Cash'],
    summary: '2 lots · 1 long-term, 1 short-term',
  },
  'lot_dispose · transfer to another account': {
    lead: 'Move 3 → Northgate Brokerage',
    meta: 'Oct 11, 2026 · FIFO · basis carries over, no gain',
    facts: {},
    summary: '1 lot · long-term',
  },
  'lot_dispose · unknown cost basis': {
    lead: 'Sell 3 @ $615.00$1,845.00',
    meta: 'Oct 11, 2026 · FIFO',
    facts: { 'Realized gain': 'unknown — no cost basis', Cash: 'not booked' },
    warn: ['Cash'],
    summary: '1 lot · long-term',
  },
  'lot_delete · open lot': {
    lead: 'Delete lot of 10 VOO$6,040.00',
    meta: 'Acquired Oct 9, 2026 · buy · $604.00 per unit',
    reason: 'Duplicate of the October 9 buy',
    facts: {},
  },
  'record_income · dividend with foreign tax withheld': {
    lead: 'Dividend received€102.00',
    meta: 'Oct 8, 2026',
    facts: { Gross: '€120.00', 'Tax withheld': '€18.00', 'In USD': '$111.18' },
  },
  'record_income · DRP reinvestment': {
    lead: 'Dividend reinvested, no cash$62.40',
    meta: 'Sep 26, 2026',
    facts: {},
  },
}

/** The card's body: everything between its header and its buttons. */
function bodyNodes(container: HTMLElement): Element[] {
  const nodes: Element[] = []
  let node = container.querySelector('.change-preview-header')?.nextElementSibling
  while (node && !node.classList.contains('change-preview-actions')) {
    nodes.push(node)
    node = node.nextElementSibling
  }
  return nodes
}

function renderBody(change: ProposedChange) {
  const { container } = render(<ChangePreview change={change} onRespond={() => {}} />)
  const nodes = bodyNodes(container)
  const all = (sel: string) =>
    nodes.flatMap((n) => [...(n.matches(sel) ? [n] : []), ...n.querySelectorAll(sel)])
  return { nodes, all }
}

describe('position, lot and income cards', () => {
  it('has an expectation for every fixture', () => {
    expect(POSITION_FIXTURES.map((f) => f.name).sort()).toEqual(Object.keys(EXPECTED).sort())
  })

  describe.each(POSITION_FIXTURES)('$name', ({ name, change }) => {
    const expected = EXPECTED[name]
    if (!expected) throw new Error(`no expectation for fixture ${name}`)

    it('states what it does, then the facts it is approved on', () => {
      const { all } = renderBody(change)

      expect(all('.ac-lead')).toHaveLength(1)
      if (expected.lead) expect(all('.ac-lead')[0]?.textContent).toBe(expected.lead)
      expect(all('.ac-meta').map((m) => m.textContent)).toEqual([expected.meta].filter(Boolean))
      expect(all('.ac-note').map((m) => m.textContent)).toEqual(
        expected.reason ? [`Reason: ${expected.reason}`] : [],
      )
      const facts = Object.fromEntries(
        all('.ac-fact').map((row) => [
          row.querySelector('.ac-fact-label')?.textContent,
          row.querySelector('.ac-fact-value')?.textContent,
        ]),
      )
      expect(facts).toEqual(expected.facts ?? {})
      expect(all('summary').map((s) => s.textContent)).toEqual([expected.summary].filter(Boolean))
    })

    it('flags what it should and nothing else', () => {
      const { all } = renderBody(change)
      const warned = all('.ac-fact')
        .filter((row) => row.querySelector('.ac-tone-warn'))
        .map((row) => row.querySelector('.ac-fact-label')?.textContent)
      expect(warned).toEqual(expected.warn ?? [])
    })

    it('uses only the approval-card primitives: no legacy class, no inline style', () => {
      const { nodes, all } = renderBody(change)
      const everything = [...nodes, ...all('*')]
      const legacy = everything.flatMap((e) => [...e.classList].filter((c) => LEGACY.test(c)))
      expect(legacy).toEqual([])
      expect(everything.filter((e) => e.hasAttribute('style'))).toEqual([])
    })

    it('is a staged card, as the backend emits it', () => {
      expect(change.grant_tool).toBeTruthy()
      expect(change.id).toBeTruthy()
    })
  })

  it('a destructive write is toned once, on its header — not again on the lead', () => {
    for (const name of ['remove · position', 'lot_delete · open lot']) {
      const change = POSITION_FIXTURES.find((f) => f.name === name)!.change
      const { container } = render(<ChangePreview change={change} onRespond={() => {}} />)
      expect(container.querySelector('.change-preview-header')?.className).toContain('--danger')
      expect(container.querySelector('.ac-lead')?.className).not.toContain('ac-tone-bad')
      cleanup()
    }
  })

  it('a loss is told apart from a gain by its tone', () => {
    const loss = POSITION_FIXTURES.find((f) => f.name.includes('a loss'))!.change
    const row = renderBody(loss)
      .all('.ac-fact')
      .find((r) => r.querySelector('.ac-fact-label')?.textContent === 'Realized gain')!
    expect(row.querySelector('.ac-fact-value')?.className).toContain('ac-tone-bad')
  })

  it('shows the basis currency the user gave, not the market quote currency', () => {
    const add = POSITION_FIXTURES.find((f) => f.name === 'add · foreign cost currency')!.change
    expect(add.display?.currency).toBe('USD')
    const text = renderBody(add)
      .nodes.map((n) => n.textContent)
      .join(' ')
    expect(text).toContain('S$80,000.00')
    expect(text).not.toContain('US$')
  })
})
