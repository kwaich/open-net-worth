// @vitest-environment jsdom
//
// Red-first parity contract for the Statement Import v3 handoff. These tests deliberately
// describe the handoff DOM/interaction rather than the legacy three-column/table structure.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn(async () => ({ ok: false })) }))

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

// The handoff puts a row's OVERRIDE controls inside its disclosure ("tapping a row is only
// ever for overriding, never for discovering"), so a locator that hands back a row for
// interaction opens it first. Idempotent: an already-open row is left alone.
const openRow = (row: HTMLElement): HTMLElement => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement | null
  if (line && line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
})

const account = {
  id: 'a1',
  name: 'Anchor',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
  color: '#22d3ee',
} as unknown as Account

const base = {
  currency: 'SGD',
  kind: 'expense',
  category: null,
  dup: false,
  preRecon: false,
  autoCat: false,
}

function handoffFixture() {
  return {
    statementId: 'st-handoff-parity',
    source: 'csv',
    drafts: [
      {
        ...base,
        rowIndex: 0,
        date: '2026-06-01',
        payee: 'Grocer',
        description: 'GROCER MARKET 8821',
        amount: -42.15,
        category: 'cat_food',
        external_refs: [{ source: 'csv', ref: 'grocer' }],
        flags: [
          {
            type: 'near_duplicate',
            band: 'strong',
            reason: 'Exact amount and date in the statement window',
            existing_id: 'ledger-grocer',
            existing: {
              note: 'Grocer',
              date: '2026-06-02',
              amount: -42,
              currency: 'SGD',
              kind: 'expense',
              category: 'cat_food',
              source: 'manual',
            },
          },
        ],
      },
      {
        ...base,
        rowIndex: 1,
        date: '2026-06-03',
        payee: 'Taxi',
        description: 'TAXI SG',
        amount: -18.4,
        external_refs: [{ source: 'csv', ref: 'taxi' }],
        flags: [
          {
            type: 'near_duplicate',
            band: 'review',
            reason: 'Possible match — description differs',
            existing_id: 'ledger-taxi',
            existing: {
              note: 'Cab ride',
              date: '2026-06-03',
              amount: -18.4,
              currency: 'SGD',
              kind: 'expense',
              source: 'agent',
            },
          },
        ],
      },
      {
        ...base,
        rowIndex: 2,
        date: '2026-06-04',
        payee: 'Anthropic',
        description: 'ANTHROPIC API',
        amount: -14.63,
        external_refs: [{ source: 'csv', ref: 'anthropic' }],
        // The settled-drift tier is an ENGINE band now (RFC 2026-07-29 §D2) — an ordinary
        // `near_duplicate` flag carrying `band: 'weak'`, not a client join over
        // `ledgerCheck.rows[].statement_twin`.
        flags: [
          {
            type: 'near_duplicate',
            band: 'weak',
            reason: 'settled 3.3% off',
            existing_id: 'ledger-anthropic',
            score: 0.31,
            alternatives: [],
            existing: {
              note: 'Anthropic Claude API',
              date: '2026-06-05',
              amount: -14.16,
              currency: 'SGD',
              kind: 'expense',
              source: 'agent',
            },
          },
        ],
      },
      {
        ...base,
        rowIndex: 3,
        date: '2026-06-05',
        payee: 'Salary',
        description: 'PAYROLL',
        amount: 2500,
        kind: 'income',
        category: 'cat_income',
        external_refs: [{ source: 'csv', ref: 'salary' }],
        flags: [],
      },
      {
        ...base,
        rowIndex: 4,
        date: '2026-06-06',
        payee: 'Blurry café',
        description: 'BL?RRY CAFE',
        amount: -9.5,
        needsEyes: true,
        confidence: 0.42,
        notes: ['Low read confidence — verify the amount'],
        external_refs: [{ source: 'csv', ref: 'blurry' }],
        flags: [],
      },
    ],
    // `ledger-anthropic` is claimed by row 2's weak flag, so the server's set difference
    // already dropped it — only the genuine orphan is left.
    ledgerCheck: {
      rows: [
        {
          id: 'ledger-orphan',
          date: '2026-06-08',
          note: 'Ledger-only coffee',
          amount: -6.2,
          currency: 'SGD',
          kind: 'expense',
          source: 'manual',
        },
      ],
      boundary: [],
      period: { start: '2026-06-01', end: '2026-06-30' },
    },
    meta: { period: { start: '2026-06-01', end: '2026-06-30' }, parse_cost_usd: 0 },
    importable: 5,
    duplicates: 0,
    skipped: [],
    total: 5,
    anomaly: {
      near_duplicate: 3,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 3,
    },
  }
}

async function openHandoff(extra: Record<string, unknown> = {}, fixture = handoffFixture()) {
  const onPreview = vi.fn().mockResolvedValue(fixture)
  const onDecisionCommit = vi.fn().mockResolvedValue({
    created: 2,
    merged: 1,
    duplicates: 0,
    skipped: 2,
    batchId: 'imp-handoff-parity',
  })
  const view = render(
    <ImportModal
      open
      accounts={[account]}
      categories={
        [
          { id: 'cat_food', key: 'cat_food', label: 'Food', kind: 'expense', color: '#f2a65a' },
          {
            id: 'cat_income',
            key: 'cat_income',
            label: 'Income',
            kind: 'income',
            color: '#4fc78a',
          },
        ] as never
      }
      catMap={
        {
          cat_food: {
            id: 'cat_food',
            key: 'cat_food',
            label: 'Food',
            kind: 'expense',
            color: '#f2a65a',
          },
          cat_income: {
            id: 'cat_income',
            key: 'cat_income',
            label: 'Income',
            kind: 'income',
            color: '#4fc78a',
          },
        } as never
      }
      defaultAccountId="a1"
      importReviewDefault="skip"
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit}
      {...extra}
    />,
  )
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(input, {
    target: {
      files: [new File(['Date,Description,Amount'], 'statement.csv', { type: 'text/csv' })],
    },
  })
  fireEvent.click(await screen.findByRole('button', { name: /Read the statement/i }))
  await waitFor(() => {
    expect(document.body.querySelector('.imp3-arow-match')?.textContent).toContain('Grocer')
  })
  return { ...view, onDecisionCommit }
}

describe('ImportModal — Statement Import handoff parity', () => {
  it('exposes the statement picker as a named keyboard-focusable button backed by a labelled input', () => {
    render(
      <ImportModal
        open
        accounts={[account]}
        defaultAccountId="a1"
        onClose={() => {}}
        onPreview={vi.fn() as never}
        onDecisionCommit={vi.fn()}
      />,
    )
    const picker = screen.getByRole('button', { name: 'Choose a statement file or drop it here' })
    const input = screen.getByLabelText('Statement file') as HTMLInputElement
    const openPicker = vi.spyOn(input, 'click')

    picker.focus()
    expect(document.activeElement).toBe(picker)
    fireEvent.click(picker)
    expect(openPicker).toHaveBeenCalledTimes(1)
    expect(document.body.querySelector('.imp-file-input')).toBe(input)
  })

  it('uses the handoff shell, hierarchy, semantic statement section, and sticky filters', async () => {
    await openHandoff()

    expect(screen.getByRole('dialog', { name: 'Import a statement' })).toBeTruthy()
    const steps = Array.from(document.body.querySelectorAll('.imp3-crumb')).map(
      (node) => node.textContent,
    )
    expect(steps).toEqual(['Choose', 'Read', 'Decide'])
    expect(screen.getByRole('button', { name: /The statement — top to bottom/ })).toBeTruthy()
    expect(document.body.querySelector('.imp3-sec.stmt')).toBeTruthy()
    const filters = screen.getByRole('navigation', { name: 'Filter statement rows' })
    expect(filters.className).toContain('imp3-filter')
    // Every row starts pre-decided with its own current default and a decided row moves to its outcome
    // chip immediately (owner: "when i tap on any decision, they should move to their
    // respective list") — nothing is left genuinely undecided, so Needs-you doesn't render
    // at all. "Taxi" defaults to skip (Left out, via importReviewDefault='skip'); "Blurry
    // café" (needs-eyes, no match) now defaults to Import (owner, 2026-07-28), same as
    // "Anthropic" (weak twin) and the plain "Salary" row.
    expect(within(filters).queryByRole('button', { name: /Needs you/ })).toBeNull()
    expect(within(filters).getByRole('button', { name: /Left out\s*1/ })).toBeTruthy()
    expect(document.body.querySelector('.imp3-visual-text')).toBeNull()
  })

  it('expands a match as compare → one decision radiogroup → full-width After import', async () => {
    await openHandoff()
    const trigger = screen.getByRole('button', { name: /Grocer, Link/i })
    fireEvent.click(trigger)
    const row = openRow(trigger.closest('.imp3-arow') as HTMLElement)

    expect(within(row).getByText('On the statement')).toBeTruthy()
    expect(within(row).getByText('In your ledger')).toBeTruthy()
    const decisions = within(row).getByRole('radiogroup', { name: /What happens to Grocer/ })
    expect(within(decisions).getByRole('radio', { name: 'Link' })).toBeTruthy()
    expect(within(decisions).getByRole('radio', { name: 'Import' })).toBeTruthy()
    expect(within(decisions).getByRole('radio', { name: 'Leave out' })).toBeTruthy()
    const compare = row.querySelector('.imp3-cmp') as HTMLElement
    const after = row.querySelector('.imp3-after') as HTMLElement
    expect(compare.contains(after)).toBe(false)
    expect(
      compare.compareDocumentPosition(decisions) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
    expect(decisions.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(document.body.querySelector('.imp-pair-body')).toBeNull()
  })

  it('keeps an existing Uncategorized category truthful when a categorized statement row merges', async () => {
    const fixture = handoffFixture()
    Object.assign(fixture.drafts[0]!.flags[0]!.existing!, { category: null })
    await openHandoff({}, fixture)
    const trigger = screen.getByRole('button', { name: /Grocer, Link/i })
    const row = openRow(trigger.closest('.imp3-arow') as HTMLElement)
    expect(row.querySelector('.imp3-arow-catcol')?.textContent).toContain('Uncategorized')
    expect((row.querySelector('.imp3-after') as HTMLElement).textContent).toContain('Uncategorized')
  })

  it('every row starts pre-decided with its own sensible default, changeable directly', async () => {
    // REWORDED 2026-07-28 (owner: "everything should have a decided default" / "when i tap
    // on any decision, they should move to their respective list, that's it!") — nothing
    // stays in a Needs-you queue by default anymore, so there IS no queue card to find here;
    // every row (including "Anthropic", the weak twin, and "Blurry café", the low-confidence
    // read with no match — which now defaults to Import, owner 2026-07-28) renders as a
    // normal matched-pair/plain row from the start, with its default already selected and
    // changeable via the same controls as any other row.
    await openHandoff()
    expect(screen.queryByRole('button', { name: /Needs you/ })).toBeNull()

    const anthropicRow = openRow(
      screen.getByText('Anthropic').closest('.imp-pair-row') as HTMLElement,
    )
    const weakDefault = within(anthropicRow).getByRole('radio', { name: 'Import' })
    expect(weakDefault.getAttribute('aria-checked')).toBe('true')
    // A weak row's link control names the question it answers (SIUM-WEAK-LINK).
    fireEvent.click(within(anthropicRow).getByRole('radio', { name: 'Same one — link' }))
    expect(
      within(anthropicRow)
        .getByRole('radio', { name: 'Same one — link' })
        .getAttribute('aria-checked'),
    ).toBe('true')

    const eyesRow = openRow(screen.getByText('Blurry café').closest('.imp3-arow') as HTMLElement)
    expect(
      within(eyesRow).getByRole('radio', { name: 'Import' }).getAttribute('aria-checked'),
    ).toBe('true')
    fireEvent.click(within(eyesRow).getByRole('radio', { name: 'Leave out' }))
    expect(within(eyesRow).getByText('Left out')).toBeTruthy()
  })

  it('keeps review, weak, and read-check Leave-out receipts as regular rows, changeable directly', async () => {
    // REWORDED 2026-07-28 (owner: "when i tap on any decision, they should move to their
    // respective list, that's it!") — the opposite of this test's old title: nothing stays
    // in a Needs-you queue at all now; "Taxi" already defaults to Left out, while "Anthropic"
    // (weak twin) and "Blurry café" (needs-eyes, no match — now defaults to Import, owner
    // 2026-07-28) are each switched to Leave out directly.
    await openHandoff()
    expect(screen.queryByRole('button', { name: /Left out\s*2/ })).toBeNull()
    expect(screen.getByRole('button', { name: /Left out\s*1/ })).toBeTruthy()

    const anthropicRow = openRow(
      screen.getByText('Anthropic').closest('.imp-pair-row') as HTMLElement,
    )
    fireEvent.click(within(anthropicRow).getByRole('radio', { name: 'Leave out' }))
    expect(
      within(anthropicRow).getByRole('radio', { name: 'Leave out' }).getAttribute('aria-checked'),
    ).toBe('true')
    expect(screen.getByRole('button', { name: /Left out\s*2/ })).toBeTruthy()

    const eyesRow = openRow(screen.getByText('Blurry café').closest('.imp3-arow') as HTMLElement)
    fireEvent.click(within(eyesRow).getByRole('radio', { name: 'Leave out' }))
    expect(screen.getByRole('button', { name: /Left out\s*3/ })).toBeTruthy()
  })

  it('shows a transfer-safe default merge and receipt in the anchor leg currency', async () => {
    const fixture = handoffFixture()
    const review = fixture.drafts[1]!
    const existing = review.flags[0]!.existing!
    Object.assign(existing, {
      kind: 'transfer',
      amount: 120,
      currency: 'USD',
      account_id: 'a1',
      from_amount: 18.4,
      from_currency: 'SGD',
      to_account_id: 'other',
      to_amount: 120,
      to_currency: 'USD',
    })
    await openHandoff({ importReviewDefault: 'merge' }, fixture)
    // Every row starts pre-decided with its own default — "Taxi" with a merge default here
    // moves straight to Will link, never a Needs-you queue at all.
    expect(screen.queryByRole('button', { name: /Needs you/ })).toBeNull()
    const row = openRow(screen.getByText('Taxi').closest('.imp3-arow') as HTMLElement)
    const merge = within(row).getByRole('radio', { name: 'Link' })
    expect(merge.getAttribute('aria-checked')).toBe('true')
  })

  it('makes Left out explicit with a visible reason, dimmed amount, and bordered overrides', async () => {
    const fixture = handoffFixture()
    fixture.drafts[4]!.category = 'cat_food'
    await openHandoff({}, fixture)

    // "Blurry café" (needs-eyes, no match) now defaults to Import (owner, 2026-07-28) —
    // explicitly leave it out to exercise the Left-out visual treatment.
    const importRow = openRow(
      screen
        .getByRole('button', { name: /^Blurry café, Import/ })
        .closest('.imp3-arow') as HTMLElement,
    )
    fireEvent.click(within(importRow).getByRole('radio', { name: 'Leave out' }))

    const trigger = screen.getByRole('button', {
      name: /^Blurry café, Left out, no category applied/,
    })
    const row = openRow(trigger.closest('.imp3-arow') as HTMLElement)
    expect(row).toBeTruthy()
    expect(within(row).getByText('Low read confidence — verify the amount')).toBeTruthy()
    expect(row.querySelector('.imp3-arow-amt.dim')).toBeTruthy()
    expect(within(row).getByRole('button', { name: 'Import' }).className).toContain('imp3-actbtn')
    expect(document.body.querySelector('.imp3-lo-why')).toBeTruthy()
  })

  it('keeps multiple pre-reconciliation rows behind the one explicit global scope control', async () => {
    const fixture = handoffFixture()
    fixture.drafts[3]!.preRecon = true
    fixture.drafts.push({
      ...base,
      rowIndex: 5,
      date: '2026-05-30',
      payee: 'Earlier fee',
      description: 'EARLIER FEE',
      amount: -7,
      external_refs: [{ source: 'csv', ref: 'earlier-fee' }],
      flags: [],
    })
    fixture.drafts[5]!.preRecon = true
    fixture.importable = 6
    fixture.total = 6
    await openHandoff({}, fixture)

    const scope = screen.getByRole('checkbox', { name: 'Include 2 earlier transactions' })
    for (const title of ['Salary', 'Earlier fee']) {
      const trigger = screen.getByRole('button', { name: new RegExp(`^${title}, Left out`) })
      fireEvent.click(trigger)
      const row = openRow(trigger.closest('.imp3-arow') as HTMLElement)
      expect(within(row).queryByRole('button', { name: 'Import' })).toBeNull()
    }

    fireEvent.click(scope)
    expect(screen.getByRole('button', { name: /^Salary, Import/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /^Earlier fee, Import/ })).toBeTruthy()
  })

  it('keeps the real ledger-only actions inside compact disclosure rows', async () => {
    await openHandoff()
    const section = screen.getByRole('button', { name: /In your ledger, not on this statement/ })
    const region = section.closest('.imp3-sec') as HTMLElement
    const rowTrigger = within(region).getByRole('button', { name: /Ledger-only coffee/ })
    expect(rowTrigger.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(rowTrigger)
    expect(rowTrigger.getAttribute('aria-expanded')).toBe('true')
    expect(within(region).getByRole('button', { name: /Open/ })).toBeTruthy()
    expect(within(region).getByRole('button', { name: /Link to a new row/ })).toBeTruthy()
    expect(within(region).getByRole('button', { name: /Delete/ })).toBeTruthy()
  })

  it('makes the Needs-you, statement, and ledger section headers operable disclosures', async () => {
    await openHandoff()
    const statement = screen.getByRole('button', { name: /The statement — top to bottom/ })
    const ledger = screen.getByRole('button', { name: /In your ledger, not on this statement/ })

    for (const [button, bodyId] of [
      [statement, 'statement-import-rows'],
      [ledger, 'statement-import-ledger-only'],
    ] as const) {
      expect(button.getAttribute('aria-expanded')).toBe('true')
      fireEvent.click(button)
      expect(button.getAttribute('aria-expanded')).toBe('false')
      expect(document.body.querySelector(`#${bodyId}`)).toBeNull()
      fireEvent.click(button)
      expect(button.getAttribute('aria-expanded')).toBe('true')
      expect(document.body.querySelector(`#${bodyId}`)).toBeTruthy()
    }

    // The Needs-you queue itself is unreachable with this fixture now — every row starts
    // pre-decided, so there's nothing left genuinely undecided to populate it. Its own
    // header-disclosure behavior is covered wherever a row IS still undecided (see
    // ImportModal.threeState.test.tsx).
    expect(screen.queryByRole('button', { name: /Needs you/ })).toBeNull()
  })

  it('states the commit outcome and write boundary in the handoff footer', async () => {
    await openHandoff()
    const footer = document.body.querySelector('.imp3-foot') as HTMLElement
    expect(footer).toBeTruthy()
    // Every row starts pre-decided with its own default — nothing is genuinely awaiting
    // anymore. "Blurry café" (needs-eyes, no match) now defaults to Import (owner,
    // 2026-07-28), so only "Taxi" (importReviewDefault='skip') is left out.
    expect(footer.textContent).toContain('Link 1 • import 3 • leave out 1')
    expect(footer.textContent).not.toContain('awaiting you')
    expect(footer.textContent).toContain('nothing is written until you confirm')
    expect(within(footer).getByRole('button', { name: 'Confirm' })).toBeTruthy()
  })
})
