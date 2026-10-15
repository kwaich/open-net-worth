import React from 'react'
import { createRoot } from 'react-dom/client'
import '../src/index.layers.css'
import '../src/index.tokens.generated.css'
import '../src/index.css'
import '../src/components/tabs/accounts/accounts.css'
import { TransactionReviewMode } from '../src/components/tabs/accounts/TransactionReviewMode'
import { fromApiAccount } from '../src/components/tabs/accounts/data'

const rows = [
  {
    id: 'qa-review-1',
    description: 'Northside Market',
    memo: 'Weekly groceries and household essentials.',
    from_amount: 86.42,
  },
  {
    id: 'qa-review-2',
    description: 'City Transit',
    memo: 'Bus and metro journeys.',
    from_amount: 3.68,
  },
].map((row) => ({
  ...row,
  date: '2026-09-19',
  kind: 'expense',
  category: row.id === 'qa-review-1' ? 'food' : 'transport',
  from_account_id: 'qa-cash',
  from_currency: 'SGD',
  base_amount: row.from_amount,
  base_currency: 'SGD',
  needs_review: true,
  cleared: false,
  source: 'automation',
  splits: [],
}))
const originalFetch = window.fetch.bind(window)
window.fetch = (input, init) =>
  String(input).includes('/transactions/recently-added')
    ? Promise.resolve(
        new Response(JSON.stringify({ transactions: rows, total: rows.length, has_more: false }), {
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    : originalFetch(input, init)
document.documentElement.setAttribute('data-theme', 'light')
createRoot(document.getElementById('root')!).render(
  <TransactionReviewMode
    open
    accounts={[
      fromApiAccount({
        id: 'qa-cash',
        name: 'Everyday Checking',
        type: 'cash',
        currency: 'SGD',
      } as never),
    ]}
    categories={[
      { id: 'food', label: 'Groceries', kind: 'expense', color: '#34b08a' },
      { id: 'transport', label: 'Transport', kind: 'expense', color: '#4488bb' },
    ]}
    payeeNames={{}}
    onClose={() => {}}
    onViewAll={() => {}}
    onEditAll={() => {}}
    onApprove={async (txn) => ({ ...txn, needsReview: false })}
    onReject={async () => {}}
    onAskLucius={async () => {}}
  />,
)
