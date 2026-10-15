# FE ↔ BE Contract (frozen for parallel build)

Base URL `/api`. Auth via existing session. All money native unless `_base` suffix (base = profile
base currency, default SGD). Balances are server-authoritative (never stored).

## Accounts
- `GET  /api/accounts?include_archived=` → `Account[]` (see fields below, incl. `balance`,
  `balance_base`, `cleared_balance`, `cleared_balance_base`).
- `POST /api/accounts` `AccountCreate` → `Account`
- `PUT  /api/accounts/{id}` `AccountUpdate` → `Account`
- `DELETE /api/accounts/{id}?force=` → deletes outright if unreferenced or only the account's own
  opening balance references it; otherwise 409 (names the transaction count) unless `force=true`,
  which cascades — deletes the account AND every referencing transaction
- `POST /api/accounts/{id}/reconcile` `{real_balance, date?, against?='cleared'}` → `{ok, adjustment, balance, cleared_balance}`
- `POST /api/accounts/describe` `{text}` → `AccountFormDraft` (structured, editable; NEVER auto-creates) **[new]**

### Account fields (existing + **new**)
```
id, name, kind(asset|liability), type, platform(=institution), currency,
initial_balance, initial_balance_date, asset_class(=band), credit_limit, last_four, color, note,
statement_close_day, payment_due_day, last_reconciled_at, archived,
connected: bool = false            **new**
group: str|null                    **new**  (cash|investments|retirement|property|credit|loans; client derives default from type)
apr: number|null                   **new**
principal: number|null             **new**
payoff_date: str|null              **new**
min_payment: number|null           **new**
apy: number|null                   **new**
locked: bool = false               **new**
# read-only computed: balance, balance_base, cleared_balance, cleared_balance_base
# holdings come from GET /api/portfolio (positions mapped by account_id), not on the account
```
`reminder_days_before` may still appear in responses for legacy stored rows, but is absent from
account create/update inputs. Reminders are managed explicitly through Automations.
Account type set (extend backend to ALL of these — labels/groups from `data.jsx`):
`checking savings cash ewallet debit_card money_market | brokerage crypto_exchange custody |
pension retirement_acct govt_fund | real_estate vehicle collectible loan_receivable other_asset |
credit_card line_of_credit | mortgage personal_loan auto_loan student_loan margin bnpl other_debt`

## Transactions
- `GET  /api/transactions?…filters…&limit=&offset=` → `{items, …, period_net?, period_net_base?}`
- `POST /api/transactions` `TransactionCreate` → txn  (expense|income|transfer|adjustment; legs per kind)
- `PUT  /api/transactions/{id}` `TransactionUpdate` → txn
- `PATCH /api/transactions/{id}/cleared` `{cleared}` → txn
- `DELETE /api/transactions/{id}` → soft-delete
- `POST /api/transactions/bulk` (bulk create/import)
- `POST /api/transactions/bulk-update` `{ids, category?, tags?, cleared?, payee_id?, needs_review?, to_account_id?}` **(+needs_review, +move)**
- `POST /api/transactions/bulk-delete` `{ids}`
- `GET  /api/transactions/{id}/activity` → `{source, addedBy, createdAt, updatedAt, activity:[{action, actor, via?, at, note?}]}` **[new]**
- (export) client-side CSV is acceptable (prototype precedent); server `GET /api/transactions/export` optional.

### Transaction fields (existing + **new** `needs_review`)
```
id, date, kind, description, category, payee_id, note, tags[], splits[],
from_account_id, from_amount, from_currency, to_account_id, to_amount, to_currency,
original_amount, original_currency, base_amount(recomputed on read), base_currency,
cleared, taxable(tri-state), source, created_at, deleted,
needs_review: bool = false         **new**  (set true for unattended/heuristic creates: AI, import, agent, email, recurring/automation — NOT bank syncs or manual; approve=clear, reject=soft-delete)
```
`source` rendered as one of: `manual bank import ai agent automation reconcile` (BE maps its
internal sources onto this set).

## Payees / Categories / Rules / Import — unchanged
Use existing `/api/payees*`, `/api/categories*`, `/api/rules*`, `/api/import*` (preview+commit
with LLM schema), `/api/category-suggestions`. See the Lucius-tools + backend explorer maps.
