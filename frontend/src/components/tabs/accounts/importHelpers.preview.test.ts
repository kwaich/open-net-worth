// Regression: the Accounts tab mapped the `/api/statements/preview` response with the WRONG field
// names (`drafts`/`total`/`skipped`/`anomaly` instead of the server's `sample`/`total_rows`/
// `skip_samples`/`anomaly_summary`). `drafts` was therefore always empty, so every statement
// upload rendered "0 to import / 0 transactions read" even though the server parsed the rows.
// This pins the mapping against the REAL wire shape (a serialised PreviewResult dataclass).

import { describe, it, expect } from 'vitest'
import { mapServerPreview, transferLine } from './importHelpers'

// The exact server shape, as seen in the browser Network tab for a Northbank PDF upload.
const SERVER_RESPONSE = {
  columns: [],
  total_rows: 22,
  importable: 22,
  duplicates: 0,
  skipped: 0,
  sample: [
    {
      date: '2026-06-01',
      payee: 'Meridian Visa Direct',
      description: 'Meridian Visa Direct',
      amount: 1000.0,
      currency: 'SGD',
      kind: 'income',
      category: '',
      original_amount: null,
      original_currency: null,
      reference: null,
      external_refs: [{ source: 'pdf', ref: 'xyz' }],
      flags: [],
    },
    {
      date: '2026-05-21',
      payee: 'Cinebar',
      description: 'MPOS*EXAMPLEHOTEL Q HOAN KIEM',
      amount: -60.36,
      currency: 'SGD',
      kind: 'expense',
      category: '',
      original_amount: 1201200,
      original_currency: 'VND',
      reference: '10293847561029384756102',
      external_refs: [{ source: 'pdf', ref: 'ref:a1:10293847561029384756102' }],
      flags: [],
    },
  ],
  skip_samples: [],
  anomaly_summary: {
    near_duplicate: 0,
    intra_batch_duplicate: 0,
    amount_anomaly: 0,
    flagged_rows: 0,
  },
}

describe('mapServerPreview', () => {
  it('reads the server sample/total_rows into drafts/total (was 0/0)', () => {
    const p = mapServerPreview(SERVER_RESPONSE as never)
    expect(p.drafts).toHaveLength(2)
    expect(p.total).toBe(22)
    expect(p.importable).toBe(22)
    expect(p.drafts[0]!.description).toBe('Meridian Visa Direct')
    expect(p.drafts[0]!.amount).toBe(1000.0)
    // Every draft has a flags array so the preview render never crashes on `.map`.
    expect(Array.isArray(p.drafts[1]!.flags)).toBe(true)
  })

  it('carries the enrichment fields (payee, foreign charge, ref, dedup id) into drafts', () => {
    const p = mapServerPreview(SERVER_RESPONSE as never)
    const row = p.drafts[1]!
    expect(row.payee).toBe('Cinebar') // clean name primary; raw kept as description
    expect(row.description).toBe('MPOS*EXAMPLEHOTEL Q HOAN KIEM')
    expect(row.original_amount).toBe(1201200)
    expect(row.original_currency).toBe('VND')
    expect(row.external_refs).toEqual([{ source: 'pdf', ref: 'ref:a1:10293847561029384756102' }])
  })

  it('maps skip_samples and anomaly_summary, not the old names', () => {
    const p = mapServerPreview({
      ...SERVER_RESPONSE,
      skip_samples: [{ line: 5, reason: 'low confidence' }],
      anomaly_summary: {
        near_duplicate: 1,
        intra_batch_duplicate: 0,
        amount_anomaly: 2,
        flagged_rows: 3,
      },
    } as never)
    expect(p.skipped).toHaveLength(1)
    expect(p.skipped[0]!.reason).toBe('low confidence')
    expect(p.anomaly.flagged_rows).toBe(3)
    expect(p.anomaly.amount_anomaly).toBe(2)
  })

  it('reads the real skipped total separately from the bounded skip_samples list', () => {
    const p = mapServerPreview({
      ...SERVER_RESPONSE,
      skipped: 26,
      skip_samples: Array.from({ length: 10 }, (_, i) => ({
        line: i + 1,
        reason: "status 'Thất bại' (not completed)",
      })),
    } as never)
    expect(p.skipped).toHaveLength(10) // the display sample stays capped
    expect(p.skippedTotal).toBe(26) // but the real count is never lost
  })

  it('degrades gracefully on an empty/garbage response', () => {
    const p = mapServerPreview({} as never)
    expect(p.drafts).toHaveLength(0)
    expect(p.total).toBe(0)
    expect(p.skipped).toEqual([])
    expect(p.anomaly.flagged_rows).toBe(0)
  })
})

describe('mapServerPreview — decision payload (RFC D7)', () => {
  const DECISION_RESPONSE = {
    ...SERVER_RESPONSE,
    statement_id: 'stmt_1',
    needs_eyes: 1,
    account_suggestion: 'acc-northbank',
    reconcile: { status: 'ok', currency: 'SGD' },
    meta: { currency: 'SGD', payment_due_date: '2026-07-10', amount_due: 4000.0 },
    format_profile: { id: 'fmt_1', learned: true },
    rows: [
      {
        index: 0,
        date: '2026-05-21',
        payee: 'Cinebar',
        description: 'MPOS*CINEBAR',
        amount: -60.36,
        currency: 'SGD',
        reference: '392815',
        external_refs: [{ source: 'pdf', ref: 'ref:a:392815' }],
        confidence: 0.97,
        needs_eyes: false,
        skip: false,
        duplicate: false,
        flags: [],
        enrich: {
          category: 'cat_x',
          category_source: 'rule',
          payee_id: 'p1',
          payee_name: 'Cinebar',
        },
      },
      {
        index: 1,
        date: '2026-05-22',
        payee: null,
        description: 'FOODIE',
        amount: -11.04,
        currency: 'SGD',
        confidence: 0.4,
        needs_eyes: true,
        skip: false,
        duplicate: false,
        flags: [],
        notes: ['amount not found in source text'],
        enrich: null,
      },
      {
        index: 2,
        date: '',
        payee: null,
        description: '',
        amount: 0,
        currency: 'SGD',
        confidence: 0,
        needs_eyes: false,
        skip: true,
        skip_reason: 'missing date',
        duplicate: false,
        flags: [],
      },
    ],
  }

  it('prefers the uncapped rows payload and carries verdicts + decision ids', () => {
    const p = mapServerPreview(DECISION_RESPONSE as never)
    expect(p.statementId).toBe('stmt_1')
    expect(p.drafts).toHaveLength(2) // skip rows excluded from drafts
    expect(p.drafts[0]!.rowIndex).toBe(0)
    expect(p.drafts[0]!.category).toBe('cat_x') // the dry-run verdict shows pre-commit
    expect(p.drafts[0]!.enrich?.category_source).toBe('rule')
    expect(p.drafts[1]!.needsEyes).toBe(true)
    expect(p.needsEyes).toBe(1)
    expect(p.reconcile?.status).toBe('ok')
    expect(p.accountSuggestion).toBe('acc-northbank')
    expect(p.formatProfile?.learned).toBe(true)
    expect(p.meta?.payment_due_date).toBe('2026-07-10')
  })

  it('falls back to the legacy sample when no rows payload exists', () => {
    const p = mapServerPreview(SERVER_RESPONSE as never)
    expect(p.statementId).toBeUndefined()
    expect(p.drafts).toHaveLength(2)
  })
})

describe('transferLine — an existing transfer names BOTH of its legs', () => {
  // Owner, 2026-08-09: "if the row in the ledger is transfer, it should show from and to
  // which account". The ledger-only panel has no anchor and no counter-hint — the statement
  // by definition contains no matching row — so `transferLine` fell through to a bare
  // "Transfer" even though the wire has carried `to_account_id` since 2026-07-18.
  const accounts = [
    { id: 'a_sgd', name: 'Meridian Multiplier (SGD)' },
    { id: 'a_eur', name: 'Meridian Multiplier (EUR)' },
  ]

  it('names from → to with no anchor or hint given', () => {
    const line = transferLine(
      { kind: 'transfer', account_id: 'a_sgd', to_account_id: 'a_eur' },
      accounts,
      {},
    )
    expect(line).toBe('Transfer • Meridian Multiplier (SGD) → Meridian Multiplier (EUR)')
  })

  it('an explicit anchor still wins over the entry-s own leg', () => {
    const line = transferLine(
      { kind: 'transfer', account_id: 'a_sgd', to_account_id: 'a_eur' },
      [...accounts, { id: 'a_other', name: 'Zephyr' }],
      {},
      { anchorAccountId: 'a_other' },
    )
    expect(line).toBe('Transfer • Meridian Multiplier (SGD) → Zephyr')
  })

  it('a self-referential other leg is ignored rather than rendered as an arrow to itself', () => {
    const line = transferLine(
      { kind: 'transfer', account_id: 'a_sgd', to_account_id: 'a_sgd' },
      accounts,
      {},
    )
    expect(line).toBe('Transfer')
  })

  it('a non-transfer is still none of its business', () => {
    expect(transferLine({ kind: 'expense', account_id: 'a_sgd' }, accounts, {})).toBeNull()
  })
})
