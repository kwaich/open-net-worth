// @vitest-environment jsdom
//
// The write-then-verify chip (ADR 2026-07-20). The confirm card asked permission BEFORE an
// ordinary ledger write; this states what a turn ALREADY wrote. So what has to be true is
// verifiability, not approval:
//
//   • collapsed, it is ONE glanceable line of entity counts;
//   • expanded, it lists entity chips without warnings or domain metadata;
//   • tapping a row opens the REAL transaction drawer, not a chat-only lookalike;
//   • Undo works, reports failure honestly, and is absent when it could not work.
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const navigateMock = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigateMock }))

const fetchMock = vi.fn()
vi.mock('../../api/client', () => ({ authFetch: (...a: unknown[]) => fetchMock(...a) }))

import { WriteReceiptChip } from './WriteReceiptChip'
import { __test__setDescriptor } from '../../lucius/entityRegistry'
import { TransactionSurfacesContext } from '../tabs/accounts/transactionSurfacesContext'
import type { ReceiptMention, WriteReceipt, WriteReceiptItem } from '../../types/chat'

beforeEach(() => {
  navigateMock.mockClear()
  fetchMock.mockReset()
})

// This project's vitest.setup.ts does not auto-cleanup, so renders would otherwise
// accumulate across tests and every getBy* would match the previous test's chip too.
afterEach(cleanup)

function mention(over: Partial<ReceiptMention> = {}): ReceiptMention {
  return {
    type: 'transaction',
    id: 'txn_1',
    label: 'Grab ride home',
    sublabel: '2026-07-19 • Transport • Checking',
    value: 'txn_1',
    icon: '↕',
    surface: 'entity',
    ...over,
  }
}

function item(over: Partial<WriteReceiptItem> = {}): WriteReceiptItem {
  return {
    mention: mention(),
    op: 'create',
    ...over,
  }
}

function receipt(over: Partial<WriteReceipt> = {}): WriteReceipt {
  return {
    created: 1,
    updated: 0,
    deleted: 0,
    total: 1,
    showing: 1,
    groups: [{ type: 'transaction', count: 1, ops: ['create'] }],
    items: [item()],
    checkpoint_id: 'ckpt_1',
    ...over,
  }
}

// ── Collapsed: one glanceable line ─────────────────────────────────────────────

it('shows the counts collapsed, with the detail hidden until asked for', () => {
  render(<WriteReceiptChip receipt={receipt()} />)
  expect(screen.getByText('1 transaction added')).toBeTruthy()
  expect(screen.queryByText('Grab ride home')).toBeNull()
})

// Persona QA round 6: "2 categorys added" and "4 lots added" (a new investor does not know what a
// lot is).
it.each([
  ['category', 'Category', 2, '2 categories added'],
  ['category', 'Category', 1, '1 category added'],
  ['lot', 'Lot', 4, '4 holdings added'],
  ['lot', 'Lot', 1, '1 holding added'],
  ['envelope', 'Envelope', 3, '3 envelopes added'],
  ['commitment_event', 'Commitment event', 2, '2 commitment events added'],
])('reads %s x%i as a plain phrase', (type, label, count, expected) => {
  __test__setDescriptor({ type, label, icon: '◆', tone: 'neutral', fields: [] })
  render(
    <WriteReceiptChip
      receipt={receipt({
        total: count,
        groups: [{ type, count, ops: ['create'] }],
        items: [item({ mention: mention({ type, id: `${type}_1`, label }) })],
      })}
    />,
  )
  expect(screen.getByText(expected)).toBeTruthy()
})

it('renders a PRE-GENERALIZATION receipt without crashing the panel', async () => {
  // Regression: tapping a receipt created BEFORE the multi-type generalization threw
  // "Cannot read properties of undefined (reading 'type')" and the error boundary took down
  // the whole chat panel. Those receipts are persisted on turn checkpoints in the OLD shape —
  // items carry inlined ledger fields (txn_id, description, category_label, account_label,
  // base_amount) and NO `mention`, and no top-level `groups`.
  //
  // The fixture below IS that stored shape, cast in — deliberately NOT built from `item()`,
  // which would hand it the `mention` production never had. Asserting on a fixture that
  // supplies the missing field is the false-green this project has already shipped three of.
  const legacy = {
    created: 0,
    updated: 1,
    deleted: 0,
    total: 1,
    showing: 1,
    amount_total: 8.4,
    base_currency: 'SGD',
    flags: [],
    items: [
      {
        txn_id: 'txn_legacy',
        op: 'update',
        date: '2026-07-20',
        description: 'Corner Soup Stall',
        category: 'cat_food',
        category_label: 'Groceries',
        account_label: 'Meridian Bank Rewards',
        base_amount: 8.4,
        base_currency: 'SGD',
        flags: [],
      },
    ],
  } as unknown as WriteReceipt

  render(<WriteReceiptChip receipt={legacy} />)
  // Expanding is the exact gesture that crashed.
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  // The row reconstructs from the legacy fields instead of throwing.
  expect(await screen.findByText('Corner Soup Stall')).toBeTruthy()
})

it('the head NAMES the type — never a bare "1 edited"', () => {
  // The ambiguity the multi-type generalization had to fix: with rows of several types,
  // "1 edited" cannot say WHICH one thing changed. The noun is now always present.
  render(
    <WriteReceiptChip
      receipt={receipt({
        updated: 1,
        created: 0,
        groups: [{ type: 'envelope', count: 1, ops: ['update'] }],
      })}
    />,
  )
  expect(screen.getByText('1 envelope edited')).toBeTruthy()
})

it('a mixed-type turn names each type, most rows first', () => {
  render(
    <WriteReceiptChip
      receipt={receipt({
        created: 4,
        total: 4,
        showing: 4,
        groups: [
          { type: 'transaction', count: 3, ops: ['create'] },
          { type: 'envelope', count: 1, ops: ['update'] },
        ],
      })}
    />,
  )
  expect(screen.getByText('3 transactions added • 1 envelope edited')).toBeTruthy()
})

it('a group with several operations reads "changed" rather than naming one of them', () => {
  render(
    <WriteReceiptChip
      receipt={receipt({
        groups: [{ type: 'transaction', count: 4, ops: ['create', 'update'] }],
      })}
    />,
  )
  expect(screen.getByText('4 transactions changed')).toBeTruthy()
})

it('caps the head at two groups and says how many more there were', () => {
  render(
    <WriteReceiptChip
      receipt={receipt({
        groups: [
          { type: 'transaction', count: 12, ops: ['create'] },
          { type: 'lot', count: 2, ops: ['create'] },
          { type: 'vest', count: 1, ops: ['create'] },
          { type: 'grant', count: 1, ops: ['create'] },
        ],
      })}
    />,
  )
  expect(screen.getByText('12 transactions added • 2 holdings added • +2 more')).toBeTruthy()
})

it('an ordinary correct write raises NO flags — the chip stays a bare count', () => {
  const { container } = render(<WriteReceiptChip receipt={receipt()} />)
  expect(container.querySelectorAll('.wr-flag')).toHaveLength(0)
})

it('ignores warning fields from an older persisted receipt', () => {
  const oldReceipt = { ...receipt(), flags: ['rule_override', 'new_payee'] } as WriteReceipt
  render(<WriteReceiptChip receipt={oldReceipt} />)
  expect(screen.queryByText('category set by a rule')).toBeNull()
  expect(screen.queryByText('new payee')).toBeNull()
})

// ── Expanded: the detail ───────────────────────────────────────────────────────

it('expands to the written rows and back', () => {
  render(<WriteReceiptChip receipt={receipt()} />)
  fireEvent.click(screen.getByText('1 transaction added'))
  expect(screen.getByText('Grab ride home')).toBeTruthy()
  expect(screen.queryByText('2026-07-19 • Transport • Checking')).toBeNull()
  expect(screen.queryByText('S$13.80')).toBeNull()
  fireEvent.click(screen.getByText('1 transaction added'))
  expect(screen.queryByText('Grab ride home')).toBeNull()
})

it('renders a NON-transaction row as its own mention chip', () => {
  // The whole point of the generalization: an envelope row is a real envelope chip, not a
  // transaction row with the money fields blanked out.
  const { container } = render(
    <WriteReceiptChip
      receipt={receipt({
        groups: [{ type: 'envelope', count: 1, ops: ['update'] }],
        items: [
          {
            mention: mention({
              type: 'envelope',
              id: 'env_1',
              label: 'Groceries',
              sublabel: 'assign • 2026-07',
              value: 'env_1',
              icon: '◫',
            }),
            op: 'update',
          },
        ],
      })}
    />,
  )
  fireEvent.click(screen.getByText('1 envelope edited'))
  expect(screen.getByText('Groceries')).toBeTruthy()
  expect(screen.queryByText('assign • 2026-07')).toBeNull()
  expect(container.querySelector('[data-receipt-type="envelope"]')).toBeTruthy()
})

it('an unresolvable row still renders, labelled by its TYPE rather than invented', () => {
  // A stale row must never be dropped (the counts would then disagree with the list) and
  // must never be given a fabricated name — absent means "no label available".
  render(
    <WriteReceiptChip
      receipt={receipt({
        items: [item({ mention: mention({ label: '', sublabel: '', stale: true }) })],
      })}
    />,
  )
  fireEvent.click(screen.getByText('1 transaction added'))
  expect(screen.getByText('Transaction')).toBeTruthy()
})

it('a type with NO deep link renders a chip that does not pretend to be clickable', () => {
  const { container } = render(
    <WriteReceiptChip
      receipt={receipt({
        groups: [{ type: 'lot', count: 1, ops: ['create'] }],
        items: [
          {
            mention: mention({ type: 'lot', id: 'lot_1', label: 'AAPL', value: 'lot_1' }),
            op: 'create',
          },
        ],
      })}
    />,
  )
  fireEvent.click(screen.getByText('1 holding added'))
  const btn = container.querySelector('.wr-row__chip') as HTMLButtonElement
  expect(btn.disabled).toBe(true)
})

it('states the true total beside a sampled list', () => {
  // A partial view must never be mistaken for the whole write — the same contract every
  // bounded read in this codebase reports.
  render(
    <WriteReceiptChip
      receipt={receipt({
        created: 40,
        total: 40,
        showing: 1,
        groups: [{ type: 'transaction', count: 40, ops: ['create'] }],
      })}
    />,
  )
  fireEvent.click(screen.getByText('40 transactions added'))
  expect(screen.getByText('Showing 1 of 40')).toBeTruthy()
})

it('says so when rows were counted but can no longer be resolved', () => {
  // An empty box would read as "nothing happened" when something did.
  render(<WriteReceiptChip receipt={receipt({ items: [], showing: 0 })} />)
  fireEvent.click(screen.getByText('1 transaction added'))
  expect(screen.getByText(/no longer available/i)).toBeTruthy()
})

// ── Tapping a row opens the REAL drawer ────────────────────────────────────────

it('opens the transaction drawer in place, by id, never navigating away', () => {
  const openTransactionById = vi.fn()
  render(
    <TransactionSurfacesContext.Provider
      value={{
        openReview: vi.fn(),
        openTransaction: vi.fn(),
        openTransactionById,
        reviewOpen: false,
      }}
    >
      <WriteReceiptChip receipt={receipt()} />
    </TransactionSurfacesContext.Provider>,
  )
  fireEvent.click(screen.getByText('1 transaction added'))
  fireEvent.click(screen.getByText('Grab ride home'))
  // The host opens its loading shell at once and fetches the row itself.
  expect(openTransactionById).toHaveBeenCalledWith('txn_1', 'Grab ride home')
  expect(fetchMock).not.toHaveBeenCalled()
  expect(navigateMock).not.toHaveBeenCalled()
})

// ── Undo — the affordance that stands in for consent here ─────────────────────

// The six undo/redo cases that lived below were removed WITH the feature (owner,
// 2026-07-20: "please stop having these undo! just show!").
//
// The chip is a RECEIPT: it reports what a turn wrote and opens any row. Recovery still
// exists exactly where it did — checkpoint auto-snapshot on every _save_raw,
// txn_audit.json, soft-delete — it is simply not this component's job. The button here
// promised something with an expiry the rest of the system does not have
// (MAX_CHECKPOINTS evicts), and the four states needed to say that honestly
// (undoable / gone / retryable-409 / error) were more machinery than the affordance was
// worth.
