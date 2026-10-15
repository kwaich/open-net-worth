// @vitest-environment jsdom
//
// Every ledger fixture through the real ChangePreview: each card must show the facts silent
// resolution decided, and be built only from the approval-card primitives — no legacy
// diff-table classes, no inline styles (docs/APPROVAL_CARDS.md).
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render } from '@testing-library/react'
import { ChangePreview } from '../../ChangePreview'
import { TXN_FIXTURES } from '../fixtures/txn'

afterEach(cleanup)

const LEGACY =
  /change-diff-table|diff-after|diff-before|change-preview-reason|change-preview-label|prompt-diff|permission-|automation-|code-run-source|realized-|dup-/

function card(name: string) {
  const fixture = TXN_FIXTURES.find((f) => f.name.startsWith(name))
  if (!fixture) throw new Error(`no fixture ${name}`)
  const { container } = render(<ChangePreview change={fixture.change} />)
  const fact = (label: string) =>
    [...container.querySelectorAll('.ac-fact')]
      .find((r) => r.querySelector('.ac-fact-label')?.textContent === label)
      ?.querySelector('.ac-fact-value') as HTMLElement | undefined
  return { container, fact }
}

const heads = (c: HTMLElement) =>
  [...c.querySelectorAll('table.ac-table th')].map((th) => th.textContent)

// The Transaction cell of row `i`: its description, then each muted line under it.
function cellLines(c: HTMLElement, i: number): string[] {
  const td = c.querySelectorAll('table.ac-table tbody tr')[i]?.children[1]
  if (!td) return []
  return [
    td.firstChild?.textContent ?? '',
    ...[...td.querySelectorAll('div')].map((d) => d.textContent ?? ''),
  ]
}

describe('ledger cards', () => {
  it.each(TXN_FIXTURES.map((f) => f.name))('%s uses only primitives', (name) => {
    const { container } = card(name)
    // The card's header and buttons belong to other owners; the body is what sits between.
    const parts = [...container.querySelector('.ac-lead')!.parentElement!.children].filter(
      (el) => !el.matches('.change-preview-header, .change-preview-actions'),
    )
    expect(parts.length).toBeGreaterThan(1)
    for (const part of parts) {
      expect(part.outerHTML).not.toMatch(LEGACY)
      expect(part.matches('[style]') || part.querySelector('[style]')).toBeFalsy()
    }
  })

  it('names the rule that set the category, and the payee that will be created', () => {
    const { container, fact } = card('txn_create · expense')
    expect(container.querySelector('.ac-lead')?.textContent).toBe(
      'Expense · Grab GrabFood orderS$13.80',
    )
    expect(fact('Category')?.textContent).toBe('Transport · set by rule: Grab -> Transport')
    expect(fact('Payee')?.textContent).toBe('Grab GrabFood order · will be created')
    expect(fact('Account')?.textContent).toBe('Meridian Bank Checking')
    expect(fact('In SGD')).toBeUndefined()
  })

  it('shows an income into its account with the payee default tier, memo and tags', () => {
    const { container, fact } = card('txn_create · income')
    expect(container.querySelector('.ac-lead')?.textContent).toBe('Income · July salaryS$4,200.00')
    expect(container.querySelector('.ac-meta')?.textContent).toBe(
      'Jul 25, 2026 · Memo: Net of CPF · Tags: payroll',
    )
    expect(fact('Category')?.textContent).toBe("Salary · Acme Pte Ltd's default")
    expect(fact('Payee')?.textContent).toBe('Acme Pte Ltd')
    expect(fact('Account')?.textContent).toBe('Meridian Bank Checking')
  })

  it('makes Uncategorized visibly warn-toned and renders warnings as warn notes', () => {
    const { container, fact } = card('txn_create · uncategorized')
    expect(fact('Category')?.textContent).toBe('Uncategorized')
    expect(fact('Category')?.className).toContain('ac-tone-warn')
    const note = container.querySelector('.ac-note')!
    expect(note.className).toContain('ac-tone-warn')
    expect(note.textContent).toMatch(/tracking start/)
    expect(fact('Payee')).toBeUndefined()
  })

  it('shows a cross-currency transfer as From → To with the received amount', () => {
    const { container, fact } = card('txn_create · cross-currency')
    expect(container.querySelector('.ac-lead')?.textContent).toBe(
      'Transfer · Fund travel walletS$500.00',
    )
    expect(fact('Transfer')?.textContent).toBe('Meridian Bank Checking → Meridian USD Wallet')
    expect(fact('Received')?.textContent).toBe('$369.50')
    expect(container.textContent).not.toMatch(/acc_/)
  })

  it('shows the base amount and historical rate for a foreign-currency leg', () => {
    const { fact } = card('txn_create · foreign-currency')
    expect(fact('In SGD')?.textContent).toBe('S$297.00 · rate 1.3500')
  })

  it('folds a split behind a summary that names its categories', () => {
    const { container, fact } = card('txn_create · split')
    expect(fact('Category')).toBeUndefined()
    const details = container.querySelector('details.ac-details') as HTMLDetailsElement
    expect(details.open).toBe(false)
    expect(details.querySelector('summary')?.textContent).toBe(
      'Split across 2 categories: Groceries, Household',
    )
    expect(details.querySelectorAll('tbody tr')).toHaveLength(2)
  })

  it('reads a category edit as one before → after row carrying its tier', () => {
    const { container, fact } = card('txn_update · category')
    const row = fact('Category')!
    expect(row.querySelector('.ac-before')?.textContent).toBe('Daily Meals')
    expect(row.textContent).toBe('Daily Meals → Transport · as specified')
    expect(container.querySelectorAll('.ac-fact-label')).toHaveLength(2)
    expect(container.textContent).not.toMatch(/Resulting transaction/)
  })

  it('shows field edits, then the resolved facts — an amount edit keeps its account', () => {
    const { container, fact } = card('txn_update · amount')
    expect(fact('From amount')?.textContent).toBe('42.5 → 45')
    expect(fact('Memo')?.textContent).toBe('— → Weekly shop')
    expect(fact('Category')?.textContent).toBe('Groceries · as specified')
    // Only an edit that moves the money (an *_account_id change) replaces the account row.
    expect(fact('Account')?.textContent).toBe('Meridian Bank Checking')
    expect(container.querySelector('.ac-lead')?.textContent).toBe('Expense · Corner GrocerS$45.00')
  })

  it('is one import card: totals, rules, warning, and a table that counts the unseen rows', () => {
    const { container, fact } = card('txn_bulk')
    // The header says "Import transactions"; the lead adds only the count.
    expect(container.querySelector('.change-preview-header')?.textContent).toBe(
      'Import transactions',
    )
    expect(container.querySelector('.ac-lead')?.textContent).toBe('248 transactions')
    expect(container.querySelector('.ac-meta')?.textContent).toBe(
      'Card statement, July · +243 more not shown',
    )
    expect(fact('Money in')?.textContent).toBe('S$3,120.00')
    expect(fact('Money out')?.textContent).toBe('S$5,417.35')
    expect(fact('Rules applied')?.textContent).toBe('Grab -> Transport (41)')
    const notes = [...container.querySelectorAll('.ac-note')].map((n) => n.textContent)
    expect(notes).toContain('1 of the 5 rows shown are Uncategorized')
    expect(notes.some((n) => /tracking start/.test(n || ''))).toBe(true)
    expect(container.querySelectorAll('table.ac-table tbody tr')).toHaveLength(5)
    // The backend names every account in the WHOLE batch, so one account is said once, as a
    // fact, even with 243 rows unseen — never repeated on each row.
    expect(fact('Account')?.textContent).toBe('Meridian Bank Checking')
    // Three columns, so the amount is never cut off on a phone; the category sits under the
    // description, muted, with its source in brackets — no "·" anywhere in the table.
    expect(heads(container)).toEqual(['Date', 'Transaction', 'Amount'])
    expect(cellLines(container, 0)).toEqual(['Grab ride', 'Transport (rule)'])
    expect(cellLines(container, 3)).toEqual(['Wire from Acme', 'Uncategorized'])
    expect(container.querySelector('table.ac-table')?.textContent).not.toContain('·')
    expect(container.querySelector('table.ac-table tbody td:last-child')?.className).toBe('ac-num')
  })

  it('an import across two accounts names each row account, and claims no single one', () => {
    const { container, fact } = card('txn_bulk · rows across two accounts')
    expect(fact('Account')).toBeUndefined()
    expect(heads(container)).toEqual(['Date', 'Transaction', 'Amount'])
    expect(cellLines(container, 1)).toEqual(['Grab ride', 'Transport (rule)', 'Meridian Savings'])
  })

  it('a card staged before account_names trusts the sample only when every row is shown', () => {
    const fixture = TXN_FIXTURES.find((f) => f.name.startsWith('txn_bulk · import'))!
    const legacy = { ...fixture.change, account_names: undefined }
    const { container } = render(<ChangePreview change={legacy} onRespond={() => {}} />)
    // 243 rows unseen: one account across the SAMPLE proves nothing about the batch.
    expect(cellLines(container, 0)).toContain('Meridian Bank Checking')
  })

  // The backend emits `automation` / `automations_applied` (ledger_preview.py); the card read
  // only the older `rule` / `rules_applied`, so a real card said "set by rule: a rule" and an
  // import never listed what fired. Fixtures now carry the real keys; an older staged card
  // with the old names must still read.
  it('still names the rule on a card staged with the older `rule` key', () => {
    const fixture = TXN_FIXTURES.find((f) => f.name.startsWith('txn_create · expense'))!
    const { automation, ...rest } = fixture.change
    const { container } = render(
      <ChangePreview change={{ ...rest, rule: automation }} onRespond={() => {}} />,
    )
    expect(container.textContent).toContain('set by rule: Grab -> Transport')
  })

  it('a fully shown import from one account names it once, as a fact', () => {
    const fixture = TXN_FIXTURES.find((f) => f.name.startsWith('txn_bulk · import'))!
    const legacy = { ...fixture.change, account_names: undefined }
    const rows = legacy.rows || []
    const { container } = render(
      <ChangePreview change={{ ...legacy, count: rows.length }} onRespond={() => {}} />,
    )
    const accountFact = [...container.querySelectorAll('.ac-fact')].find(
      (r) => r.querySelector('.ac-fact-label')?.textContent === 'Account',
    )
    expect(accountFact?.querySelector('.ac-fact-value')?.textContent).toBe('Meridian Bank Checking')
    expect(cellLines(container, 0)).toEqual(['Grab ride', 'Transport (rule)'])
  })

  it('an investment or adjustment leg says which way the money moves', () => {
    const base = TXN_FIXTURES.find((f) => f.name.startsWith('txn_create · expense'))!.change
    const leg = base.from!
    for (const [kind, legs, label] of [
      ['investment', { from: leg, to: null }, 'From'],
      ['adjustment', { from: null, to: leg }, 'To'],
      ['expense', { from: leg, to: null }, 'Account'],
    ] as const) {
      const { container } = render(
        <ChangePreview change={{ ...base, txn_kind: kind, ...legs }} onRespond={() => {}} />,
      )
      const labels = [...container.querySelectorAll('.ac-fact-label')].map((l) => l.textContent)
      expect(labels, kind).toContain(label)
      cleanup()
    }
  })
})
