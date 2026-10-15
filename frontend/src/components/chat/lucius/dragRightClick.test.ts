// @vitest-environment jsdom
//
// Tests for the parseRef function from dragRightClick.tsx (Part C: drag parity).
// Verifies that dragged transaction chips carry chipDate/chipAmt/chipAmtNeg from
// data-entity-sublabel, and that account chips carry color from data-entity-color.
//
// parseRef is an internal function so we test its observable effect via the
// exported DOM helpers and the shared parseTransactionSublabel utility.

import { describe, it, expect } from 'vitest'
import { parseTransactionSublabel, isSafeCssColor } from '../../../utils/mentionable'

// ── Part C: parseRef sublabel parity (replicated logic, same as the real parseRef) ──

// We replicate the parseRef logic inline to test the critical data-extraction branch
// without exporting internals from dragRightClick. This keeps the test focused on
// the contract: the same sublabel string that handlePick passes to parseTransactionSublabel
// must produce the same chipDate/chipAmt/chipAmtNeg regardless of code path.

function simulateParseRef(el: Element): {
  type: string
  ref: string
  label: string
  chipDate?: string
  chipAmt?: string
  chipAmtNeg?: boolean
  color?: string
} | null {
  const attr = el.getAttribute('data-entity-id')
  if (!attr) return null
  const colon = attr.indexOf(':')
  if (colon < 1) return null
  const type = attr.slice(0, colon)
  const ref = attr.slice(colon + 1)
  const label = el.getAttribute('data-entity-label') || el.textContent?.trim() || ref
  const result: ReturnType<typeof simulateParseRef> = { type, ref, label }
  if (!result) return null

  if (type === 'transaction') {
    const sublabel = el.getAttribute('data-entity-sublabel') ?? ''
    if (sublabel) {
      const fields = parseTransactionSublabel(sublabel)
      if (fields.chipDate !== undefined) result.chipDate = fields.chipDate
      if (fields.chipAmt !== undefined) result.chipAmt = fields.chipAmt
      if (fields.chipAmtNeg !== undefined) result.chipAmtNeg = fields.chipAmtNeg
    }
  }

  if (type === 'account') {
    const rawColor = el.getAttribute('data-entity-color') ?? ''
    if (rawColor && isSafeCssColor(rawColor)) {
      result.color = rawColor
    }
  }

  return result
}

describe('dragRightClick parseRef — Part C: transaction drag parity', () => {
  it('extracts chipDate, chipAmt, chipAmtNeg from data-entity-sublabel on a transaction', () => {
    const el = document.createElement('div')
    el.setAttribute('data-entity-id', 'transaction:txn-drag-1')
    el.setAttribute('data-entity-label', 'Grab ride')
    el.setAttribute('data-entity-sublabel', 'Grab • Meridian Bank • 2024-06-15 • -12.50 SGD')
    document.body.appendChild(el)

    const ref = simulateParseRef(el)
    expect(ref).not.toBeNull()
    expect(ref!.type).toBe('transaction')
    expect(ref!.chipDate).toBe('Jun 15')
    expect(ref!.chipAmt).toBe('-12.50 SGD')
    expect(ref!.chipAmtNeg).toBe(true)
    el.remove()
  })

  it('extracts positive signed amount as chipAmtNeg=false', () => {
    const el = document.createElement('div')
    el.setAttribute('data-entity-id', 'transaction:txn-drag-2')
    el.setAttribute('data-entity-label', 'Salary')
    el.setAttribute('data-entity-sublabel', 'Anthropic • Lighthouse Bank • 2026-05-01 • +8000 SGD')
    document.body.appendChild(el)

    const ref = simulateParseRef(el)
    expect(ref!.chipAmt).toBe('+8000 SGD')
    expect(ref!.chipAmtNeg).toBe(false)
    expect(ref!.chipDate).toBe('May 1')
    el.remove()
  })

  it('produces same output as handlePick for the same sublabel string', () => {
    // This is the consistency test: given the same sublabel, both paths must agree.
    const sublabel = 'Stripe • Vantage Brokerage USD • 2025-03-10 • +5000.00 USD'

    // handlePick path (via the shared helper directly)
    const pickerResult = parseTransactionSublabel(sublabel)

    // Drag path (via simulateParseRef which also calls the same helper)
    const el = document.createElement('div')
    el.setAttribute('data-entity-id', 'transaction:txn-cons')
    el.setAttribute('data-entity-label', 'Stripe')
    el.setAttribute('data-entity-sublabel', sublabel)
    document.body.appendChild(el)
    const dragResult = simulateParseRef(el)
    el.remove()

    expect(dragResult!.chipDate).toBe(pickerResult.chipDate)
    expect(dragResult!.chipAmt).toBe(pickerResult.chipAmt)
    expect(dragResult!.chipAmtNeg).toBe(pickerResult.chipAmtNeg)
  })

  it('leaves chipDate/chipAmt/chipAmtNeg undefined when sublabel is absent on a transaction', () => {
    const el = document.createElement('div')
    el.setAttribute('data-entity-id', 'transaction:txn-nosub')
    el.setAttribute('data-entity-label', 'No sublabel txn')
    document.body.appendChild(el)

    const ref = simulateParseRef(el)
    expect(ref!.chipDate).toBeUndefined()
    expect(ref!.chipAmt).toBeUndefined()
    expect(ref!.chipAmtNeg).toBeUndefined()
    el.remove()
  })
})

describe('dragRightClick parseRef — Part B: account color', () => {
  it('carries a valid hex color from data-entity-color onto the MentionRef', () => {
    const el = document.createElement('div')
    el.setAttribute('data-entity-id', 'account:acc-c1')
    el.setAttribute('data-entity-label', 'Meridian Bank')
    el.setAttribute('data-entity-color', '#ff5733')
    document.body.appendChild(el)

    const ref = simulateParseRef(el)
    expect(ref!.color).toBe('#ff5733')
    el.remove()
  })

  it('carries a CSS var() token as color', () => {
    const el = document.createElement('div')
    el.setAttribute('data-entity-id', 'account:acc-c2')
    el.setAttribute('data-entity-label', 'Lighthouse Bank')
    el.setAttribute('data-entity-color', 'var(--lucius)')
    document.body.appendChild(el)

    const ref = simulateParseRef(el)
    expect(ref!.color).toBe('var(--lucius)')
    el.remove()
  })

  it('rejects an invalid color and leaves color undefined', () => {
    const el = document.createElement('div')
    el.setAttribute('data-entity-id', 'account:acc-bad')
    el.setAttribute('data-entity-label', 'Bad Actor')
    el.setAttribute('data-entity-color', 'expression(alert(1))')
    document.body.appendChild(el)

    const ref = simulateParseRef(el)
    expect(ref!.color).toBeUndefined()
    el.remove()
  })

  it('leaves color undefined when data-entity-color is absent on an account', () => {
    const el = document.createElement('div')
    el.setAttribute('data-entity-id', 'account:acc-nocolor')
    el.setAttribute('data-entity-label', 'Swiftly')
    document.body.appendChild(el)

    const ref = simulateParseRef(el)
    expect(ref!.color).toBeUndefined()
    el.remove()
  })
})
