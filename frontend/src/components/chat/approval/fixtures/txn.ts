import type { ProposedChange } from '../../../../types/chat'
import type { CardFixture } from './types'

/** Synthetic ledger cards in the exact shape `backend/services/ledger_preview.py` stages
 * (preview_create / preview_update / preview_bulk). Names and figures are invented. */

const staged = (name: string, grantTool: string, change: Record<string, unknown>): CardFixture => {
  const id = `toolu_${name.replace(/[^a-z0-9]+/gi, '_').toLowerCase()}`
  return {
    name,
    change: { id, grant_tool: grantTool, ...change } as ProposedChange,
  }
}

const CHECKING = {
  account_id: 'acc_chk',
  account_name: 'Meridian Bank Checking',
  currency: 'SGD',
}

const resolved = {
  memo: '',
  automation: null,
  payee: null,
  to: null,
  splits: null,
  tags: [],
  warnings: [],
}

const sgd = (amount: number) => ({ amount, currency: 'SGD', fx_rate: 1.0 })

const bulkRows = [
  ['Grab ride', 'Transport', 'rule', 14.2],
  ['Grab ride', 'Transport', 'rule', 9.8],
  ['Corner Grocer', 'Groceries', 'payee_default', 42.5],
  ['Wire from Acme', 'Uncategorized', 'none', 120],
  ['City Gym', 'Fitness', 'explicit', 65],
].map(([description, category_label, category_source, amount], i) => ({
  date: `2026-07-${String(10 + i).padStart(2, '0')}`,
  description,
  category_label,
  category_source,
  amount,
  currency: 'SGD',
  base_amount: amount,
  account_name: CHECKING.account_name,
}))

export const TXN_FIXTURES: CardFixture[] = [
  staged('txn_create · expense, category set by a rule', 'add_transactions', {
    ...resolved,
    type: 'txn_create',
    txn_kind: 'expense',
    date: '2026-07-19',
    description: 'Grab GrabFood order',
    category: { id: 'cat_transport', label: 'Transport', source: 'rule' },
    automation: { id: 'rule_grab', name: 'Grab -> Transport' },
    payee: { id: null, name: 'Grab GrabFood order', is_new: true },
    from: { ...CHECKING, amount: 13.8 },
    base: sgd(13.8),
  }),
  staged('txn_create · income, payee default category', 'add_transactions', {
    ...resolved,
    type: 'txn_create',
    txn_kind: 'income',
    date: '2026-07-25',
    description: 'July salary',
    memo: 'Net of CPF',
    category: { id: 'cat_salary', label: 'Salary', source: 'payee_default' },
    payee: { id: 'pay_acme', name: 'Acme Pte Ltd', is_new: false },
    from: null,
    to: { ...CHECKING, amount: 4200 },
    base: sgd(4200),
    tags: ['payroll'],
  }),
  staged('txn_create · uncategorized with a warning', 'add_transactions', {
    ...resolved,
    type: 'txn_create',
    txn_kind: 'expense',
    date: '2026-03-02',
    description: 'Corner Grocer',
    category: { id: null, label: 'Uncategorized', source: 'none' },
    payee: { id: 'pay_grocer', name: 'Corner Grocer', is_new: false },
    from: { ...CHECKING, amount: 42.5 },
    base: sgd(42.5),
    warnings: ["Dated before Meridian Bank Checking's tracking start."],
  }),
  staged('txn_create · cross-currency transfer', 'add_transactions', {
    ...resolved,
    type: 'txn_create',
    txn_kind: 'transfer',
    date: '2026-07-19',
    description: 'Fund travel wallet',
    category: { id: null, label: 'Uncategorized', source: 'none' },
    from: { ...CHECKING, amount: 500 },
    to: {
      account_id: 'acc_usd',
      account_name: 'Meridian USD Wallet',
      amount: 369.5,
      currency: 'USD',
    },
    base: sgd(500),
  }),
  staged('txn_create · foreign-currency expense with fx', 'add_transactions', {
    ...resolved,
    type: 'txn_create',
    txn_kind: 'expense',
    date: '2026-07-20',
    description: 'Harbour Hotel',
    category: { id: 'cat_travel', label: 'Travel', source: 'explicit' },
    payee: { id: 'pay_harbour', name: 'Harbour Hotel', is_new: false },
    from: {
      account_id: 'acc_usd',
      account_name: 'Meridian USD Wallet',
      amount: 220,
      currency: 'USD',
    },
    base: { amount: 297.0, currency: 'SGD', fx_rate: 1.35 },
  }),
  staged('txn_create · split across categories', 'add_transactions', {
    ...resolved,
    type: 'txn_create',
    txn_kind: 'expense',
    date: '2026-07-21',
    description: 'Big Mart',
    category: { id: null, label: 'Split', source: 'split' },
    payee: { id: 'pay_bigmart', name: 'Big Mart', is_new: false },
    from: { ...CHECKING, amount: 86 },
    base: sgd(86),
    splits: [
      { category_id: 'cat_groceries', category_label: 'Groceries', amount: 62, note: null },
      { category_id: 'cat_household', category_label: 'Household', amount: 24, note: null },
    ],
  }),
  staged('txn_update · category change', 'update_transactions', {
    ...resolved,
    type: 'txn_update',
    transaction_id: 'txn_984659150000',
    txn_kind: 'expense',
    date: '2026-07-19',
    description: 'Lunch',
    fields: [{ field: 'category', label: 'Category', before: 'Daily Meals', after: 'Transport' }],
    category: { id: 'cat_transport', label: 'Transport', source: 'explicit' },
    payee: { id: 'pay_lunch', name: 'Lunch', is_new: false },
    from: { ...CHECKING, amount: 12 },
    base: sgd(12),
  }),
  staged('txn_update · amount and memo', 'update_transactions', {
    ...resolved,
    type: 'txn_update',
    transaction_id: 'txn_984659150001',
    txn_kind: 'expense',
    date: '2026-07-19',
    description: 'Corner Grocer',
    memo: 'Weekly shop',
    fields: [
      { field: 'from_amount', label: 'From amount', before: 42.5, after: 45 },
      { field: 'memo', label: 'Memo', before: null, after: 'Weekly shop' },
    ],
    category: { id: 'cat_groceries', label: 'Groceries', source: 'explicit' },
    payee: { id: 'pay_grocer', name: 'Corner Grocer', is_new: false },
    from: { ...CHECKING, amount: 45 },
    base: sgd(45),
  }),
  staged('txn_bulk · import with a rule and a warning', 'add_transactions', {
    type: 'txn_bulk',
    count: 248,
    import_description: 'Card statement, July',
    rows: bulkRows,
    truncated: true,
    totals: { base_currency: 'SGD', inflow_base: 3120, outflow_base: 5417.35 },
    automations_applied: [{ id: 'rule_grab', name: 'Grab -> Transport', count: 41 }],
    account_names: [CHECKING.account_name],
    warnings: ["Dated before Meridian Bank Checking's tracking start."],
  }),
  staged('txn_bulk · rows across two accounts', 'add_transactions', {
    type: 'txn_bulk',
    count: 5,
    import_description: 'Bank export, July',
    rows: bulkRows.map((r, i) => (i % 2 ? { ...r, account_name: 'Meridian Savings' } : r)),
    truncated: false,
    totals: { base_currency: 'SGD', inflow_base: 120, outflow_base: 131.5 },
    account_names: [CHECKING.account_name, 'Meridian Savings'],
  }),
]
