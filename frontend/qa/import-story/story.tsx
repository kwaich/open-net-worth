// qa/import-story/story.tsx — DEV-ONLY visual harness for the Statement Import Decide step.
//
//   cd frontend && npx vite --port 5199
//   open http://localhost:5199/qa/import-story/
//
// Mounts the REAL production ImportModal already parked on the Decide step, fed a fixture
// preview that mirrors the handoff prototype's Vietcombank scenario (matched / review / weak /
// needs-eyes / new / Lucius-routed / left-out / ledger-only / boundary / FX-original rows) so
// the rendered production DOM can be diffed against
// `frontend/design_handoffs/statement_import_handoff/prototype`.
//
// The app never imports this file, and it makes NO network calls — every prop is a stub, so it
// can never touch a live backend. It is a rendering fixture only; behavioural truth lives in
// the vitest suites and the browser-use E2E run against a disposable instance.
import { createRoot } from 'react-dom/client'
import { useEffect, useState } from 'react'
import { ImportModal } from '../../src/components/tabs/accounts/ImportModal'
import type { ImportPreviewResult } from '../../src/components/tabs/accounts/importHelpers'
import type { Account, Category } from '../../src/components/tabs/accounts/types'
// Same CSS chain, in the same order, as src/main.tsx — layers first, then tokens.
import '../../src/index.layers.css'
import '../../src/index.tokens.generated.css'
import '../../src/index.breakpoints.generated.css'
import '../../src/index.css'
import '../../src/components/tabs/accounts/accounts.css'

const accounts = [
  { id: 'vcb', name: 'Vietcombank', kind: 'asset', type: 'bank', currency: 'VND', color: '#3b86dd', balance: 0 },
  { id: 'momo', name: 'MoMo Wallet', kind: 'asset', type: 'wallet', currency: 'VND', color: '#dd6fa8', balance: 0 },
  { id: 'wise-sgd', name: 'Wise SGD', kind: 'asset', type: 'bank', currency: 'SGD', color: '#34b08a', balance: 0 },
] as unknown as Account[]

const categories = [
  { id: 'cat_dining', label: 'Daily Meals', kind: 'expense', color: '#e0a032' },
  { id: 'cat_transport', label: 'Transport', kind: 'expense', color: '#3b86dd' },
  { id: 'cat_subs', label: 'Subscriptions', kind: 'expense', color: '#9a7fd0' },
  { id: 'cat_shopping', label: 'Shopping', kind: 'expense', color: '#dd6fa8' },
  { id: 'cat_nightlife', label: 'Drinks & Nightlife', kind: 'expense', color: '#a855f7' },
] as unknown as Category[]
const catMap = Object.fromEntries(categories.map(c => [(c as unknown as { id: string }).id, c])) as Record<string, Category>

type Draft = ImportPreviewResult['drafts'][number]
const row = (i: number, o: Partial<Draft> & { date: string; description: string; amount: number }): Draft => ({
  payee: null, kind: o.amount < 0 ? 'expense' : 'income', category: null, currency: 'VND',
  flags: [], dup: false, preRecon: false, autoCat: false, rowIndex: i, ...o,
} as Draft)

const ex = (o: Record<string, unknown>) => ({ note: '', amount: 0, currency: 'VND', kind: 'txn', ...o })

const PREVIEW: ImportPreviewResult = {
  statementId: 'stmt_story',
  total: 13, importable: 12, duplicates: 0,
  drafts: [
    row(0, {
      date: '2026-06-01', payee: 'MoMo top-up', description: 'Ecom.EW00000000000002.MOMO.0300000000.CashIn.46afea4b2522',
      amount: -200000, destId: 'vcb',
      routed: { rule_id: 'lucius_pass', from_account_id: 'vcb', from_account_name: 'Vietcombank', to_account_id: 'momo', to_account_name: 'MoMo Wallet' },
      lucius: { op: 'route_transfer', reason: 'CashIn = top-up, never an expense — per your saved instruction.', account_id: 'momo' },
    }),
    row(1, {
      date: '2026-06-01', payee: 'Cha ca la vong', description: 'MBVCB.10000000002.857552.td thanh toan example restaurant',
      amount: -800000, category: 'cat_dining', destId: 'vcb',
      flags: [{ type: 'near_duplicate', band: 'strong', reason: 'Same amount, same day — and the wording matches.', existing_id: 'tx_1', score: 0.98,
        existing: ex({ note: 'Thanh toan example restaurant (family food payment)', amount: -800000, date: '2026-06-01', category: 'cat_dining', source: 'lucius' }) }],
    }),
    row(2, {
      date: '2026-06-01', payee: 'MoMo top-up', description: 'Ecom.EW00000000000001.MOMO.0300000000.CashIn.6625252b061b',
      amount: -98000, destId: 'vcb',
      routed: { rule_id: 'lucius_pass', from_account_id: 'vcb', from_account_name: 'Vietcombank', to_account_id: 'momo', to_account_name: 'MoMo Wallet' },
      flags: [{ type: 'transfer_pair', band: 'strong', reason: 'That exact transfer is already booked.', existing_id: 'tx_2',
        existing: ex({ note: 'MoMo top-up', amount: -98000, date: '2026-06-01', kind: 'transfer', account_id: 'vcb', source: 'agent' }) }],
    }),
    row(3, {
      date: '2026-06-01', payee: 'Wise transfer in', description: '6172IBT1kCYE2DIP Wise transfer #1000000001 FROM DBS',
      amount: 2000000, kind: 'income', destId: 'vcb',
      flags: [{ type: 'transfer_pair', band: 'strong', reason: 'The landing leg of your Wise transfer.', existing_id: 'tx_3',
        existing: ex({ note: 'Transfer to Vietcombank — Wise', amount: 2000000, date: '2026-06-01', kind: 'transfer', account_id: 'wise-sgd', source: 'lucius' }) }],
    }),
    row(4, {
      date: '2026-06-02', payee: 'Grab', description: 'GRAB*FOOD SG 00000000000007',
      amount: -166000, category: 'cat_transport', destId: 'vcb',
      flags: [{ type: 'near_duplicate', band: 'review', reason: 'Probably your GrabFood entry from the same day — it settled ₫2,000 lower.', existing_id: 'tx_4', score: 0.71,
        existing: ex({ note: 'GrabFood — two orders', amount: -168000, date: '2026-06-02', category: 'cat_transport', source: 'agent' }) }],
    }),
    row(5, {
      date: '2026-06-02', payee: 'Cocktails', description: 'MBVCB.10000000003.AAAA1111BBBB2222.thanh toan cocktails',
      amount: -400000, category: 'cat_nightlife', destId: 'vcb',
      flags: [{ type: 'near_duplicate', band: 'strong', reason: 'The bank reference appears on both — certain.', existing_id: 'tx_5',
        existing: ex({ note: 'Cocktails — Le Q. C. (TPBank)', amount: -400000, date: '2026-06-02', category: 'cat_nightlife', source: 'lucius' }) }],
    }),
    row(6, {
      date: '2026-06-02', payee: 'Anthropic', description: 'ANTHROPIC SAAS ANTHROPIC.COM CA USD 2.50',
      amount: -60360, category: 'cat_subs', autoCat: true, destId: 'vcb',
      original_amount: -2.5, original_currency: 'USD',
    }),
    row(7, { date: '2026-06-04', payee: 'Example Ride', description: 'MBVCB.10000000004 td thanh toan green sm', amount: -280000, category: 'cat_transport', autoCat: true, destId: 'vcb' }),
    row(8, {
      date: '2026-06-08', payee: 'Shopee', description: 'SHOPEE*1000000013 SG · charged 08 Jun · refunded in full 09 Jun',
      amount: -129000, category: 'cat_shopping', destId: 'vcb',
      lucius: { op: 'skip', reason: 'Refunded in full on this statement — nothing actually left your account.' },
    }),
    row(9, {
      date: '2026-06-14', payee: 'ExampleSub', description: 'UHHT.866601 DG:example-subscription.com/help Tinde',
      amount: -150000, category: 'cat_subs', autoCat: true, destId: 'vcb',
    }),
    row(10, {
      date: '2026-06-20', payee: 'Atome', description: 'ATOME*INSTALMENT 2/3 SG', amount: -32500,
      category: 'cat_shopping', autoCat: true, destId: 'vcb', needsEyes: true, confidence: 0.62,
      notes: ['The amount was printed across two lines on the PDF — worth a glance before it books.'],
    }),
    row(11, {
      date: '2026-06-22', payee: 'Shopee', description: 'SHOPEE*1000000011 SG', amount: -45900,
      category: 'cat_shopping', autoCat: true, destId: 'vcb',
    }),
    row(12, {
      date: '2026-05-31', payee: 'MoMo top-up', description: 'Ecom.EW00000000000005.MOMO.0300000000.CashIn.aa11bb22cc33',
      amount: -149000, destId: 'vcb', dup: true,
      flags: [{ type: 'near_duplicate', band: 'strong', reason: 'Booked by a previous import of this file.', existing_id: 'tx_9',
        existing: ex({ note: 'MoMo top-up', amount: -149000, date: '2026-05-31', kind: 'transfer' }) }],
    }),
  ],
  skipped: [],
  anomaly: { near_duplicate: 4, intra_batch_duplicate: 0, amount_anomaly: 0, flagged_rows: 4 },
  meta: { currency: 'VND', period_start: '2026-06-01', period_end: '2026-06-29' },
  reconcile: { status: 'ok', currency: 'VND' },
  llmCostUsd: 0,
  resolvedAccountId: 'vcb',
  instructions: { text: 'Rows with MOMO.0300000000 (CashIn/CashOut) are internal transfers between this Vietcombank account and my MoMo Wallet — never expenses or income.', lines: 1, used_in_parse: true, remembered: true },
  luciusPass: { applied: 6, routes: 5, categories: 0, skips: 1, fresh: false },
  ledgerCheck: {
    period: { start: '2026-06-01', end: '2026-06-29' },
    rows: [
      { id: 'lz', date: '2026-06-11', note: 'TD subscription renewal', amount: -250000, currency: 'VND', source: 'email', category: 'cat_subs',
        statement_twin: { description: 'ExampleSub', amount: -150000, date: '2026-06-14', row_index: 9, exact: false } },
      { id: 'la', date: '2026-06-03', note: 'Balance adjustment — MoMo Wallet', amount: 79000, currency: 'VND', source: 'lucius' },
    ],
    boundary: [{ id: 'lb', date: '2026-06-30', note: 'Pho — near cut-off', amount: -45000, currency: 'VND' }],
    twin_eligible: { '11': ['lz', 'la'], '4': ['lz'] },
  },
} as unknown as ImportPreviewResult

function Story() {
  const [open, setOpen] = useState(true)
  const q = new URLSearchParams(location.search)
  useEffect(() => {
    const theme = new URLSearchParams(location.search).get('theme')
    if (theme === 'light') document.documentElement.setAttribute('data-theme', 'light')
  }, [])
  // Choose-step captures: `?acct=none` drops the pre-picked account so the no-account state
  // shows (no chip, CTA disabled, "Select account" required — 2026-07-27: an account is always
  // explicit), and `?peek=known` makes the source-peek probe answer "known" so the handoff's
  // "I know this source." banner + its saved instructions render.
  const peekKnown = q.get('peek') === 'known'
  // `onSourcePeek` is ALWAYS wired here, like the real AccountsTab: an absent probe is its own
  // state (`unavailable`, which keeps the re-read escape hatch visible), so leaving it out
  // would capture a screen production never shows.
  return (
    <ImportModal open={open} onClose={() => setOpen(false)} accounts={accounts}
      defaultAccountId={q.get('acct') === 'none' ? undefined : 'vcb'}
      onSourcePeek={peekKnown
        ? async () => ({ known: true, instructions: 'Rows with MOMO.0300000000 (CashIn/CashOut) are internal transfers between this Vietcombank account and my MoMo Wallet — never expenses or income.', formatLabel: 'STT No. · Ngày/TNX Date' })
        : async () => ({ known: false, instructions: '', formatLabel: null })}
      categories={categories} catMap={catMap}
      onPreview={async () => {
        // `?slow=1` holds the Read step open long enough to capture its rotating phase line.
        if (q.get('slow')) await new Promise(r => setTimeout(r, 20_000))
        return PREVIEW
      }}
      onDecisionCommit={async () => ({ created: 6, merged: 5, duplicates: 1, skipped: 1, importBatchId: 'batch_story' })}
      onMarkImportReviewed={async () => ({ updated: 6 })}
      importReviewDefault="merge" baseCurrency="VND" rates={{ VND: 1, SGD: 17000, USD: 24000 }} />
  )
}

createRoot(document.getElementById('root')!).render(<Story />)
