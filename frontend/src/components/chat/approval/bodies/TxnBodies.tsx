import type { ReactNode } from 'react'
import type { ProposedChange } from '../../../../types/chat'
import { fAmountNative, fCurrency, fDate } from '../../../../utils/format'
import { renderGenericValue } from '../cardTypes'
import {
  ApprovalChange,
  ApprovalDetails,
  ApprovalFact,
  ApprovalFacts,
  ApprovalLead,
  ApprovalMeta,
  ApprovalNote,
  ApprovalTable,
} from '../ApprovalCard'

type TxnCategory = NonNullable<ProposedChange['category']>
type TxnLeg = NonNullable<ProposedChange['from']>
type BulkRow = NonNullable<ProposedChange['rows']>[number]

const KIND_LABEL: Record<string, string> = {
  expense: 'Expense',
  income: 'Income',
  transfer: 'Transfer',
  investment: 'Investment',
  adjustment: 'Adjustment',
}

// The update diff's account-shaped fields — when any of them is in the diff, the account
// is already on a change row and the resulting-account fact would say it twice.
// An edit that MOVES the money names the accounts in its diff; an amount edit does not, so the
// accounts stay as facts.
const ACCOUNT_FIELDS = ['from_account_id', 'to_account_id']

// Which tier of the create-time resolution decided the category — the incident this card
// exists to fix, so it is always said in words, never left for the user to infer.
function categorySource(
  category: TxnCategory,
  rule: ProposedChange['rule'],
  payee: ProposedChange['payee'],
): string {
  switch (category.source) {
    case 'rule':
      return `set by rule: ${rule?.name ?? 'a rule'}`
    case 'payee_default':
      return `${payee?.name ?? 'the payee'}'s default`
    case 'explicit':
      return 'as specified'
    default:
      return 'Uncategorized'
  }
}

// A txn_bulk sample row only carries the bare source string (no rule/payee name — the
// sample is a display cap, not the full resolution detail).
function bulkSource(source: string): string {
  switch (source) {
    case 'rule':
      return 'rule'
    case 'payee_default':
      return 'payee default'
    case 'explicit':
      return 'as specified'
    case 'split':
      return 'split'
    default:
      return 'Uncategorized'
  }
}

const legCurrencyOf = (change: ProposedChange) =>
  change.from?.currency ?? change.to?.currency ?? change.base?.currency ?? null

// The leg that carries the amount the user is approving: the money going out, else the
// money coming in.
const primaryLeg = (change: ProposedChange): TxnLeg | null =>
  change.from?.amount != null ? change.from : change.to?.amount != null ? change.to : null

function TxnLead({ change }: { change: ProposedChange }) {
  const leg = primaryLeg(change)
  const kind = KIND_LABEL[change.txn_kind ?? ''] ?? 'Transaction'
  const subject = change.description || change.payee?.name
  return (
    <ApprovalLead figure={leg && fCurrency(leg.amount!, leg.currency || '')}>
      {subject ? `${kind} · ${subject}` : kind}
    </ApprovalLead>
  )
}

function TxnMeta({ change }: { change: ProposedChange }) {
  return (
    <ApprovalMeta
      items={[
        change.date && fDate(change.date, 'short', 'always'),
        change.memo && `Memo: ${change.memo}`,
        change.tags && change.tags.length > 0 && `Tags: ${change.tags.join(', ')}`,
      ]}
    />
  )
}

// The resolved category + its tier. Uncategorized is warn-toned: a user who wanted a
// category needs to SEE that none was applied. A split defers to SplitsDetails instead.
function categoryText(change: ProposedChange): string | null {
  const { category } = change
  if (!category || category.source === 'split') return null
  if (category.source === 'none') return category.label || 'Uncategorized'
  return `${category.label} · ${categorySource(category, change.automation ?? change.rule, change.payee)}`
}

function CategoryFact({ change }: { change: ProposedChange }) {
  const text = categoryText(change)
  if (text == null) return null
  const none = change.category?.source === 'none'
  return (
    <ApprovalFact
      label="Category"
      tone={none ? 'warn' : undefined}
      title={
        none
          ? 'No rule, payee default or category given, so it is stored Uncategorized.'
          : undefined
      }
    >
      {text}
    </ApprovalFact>
  )
}

// Only worth a row when the payee is new (the write will mint it) or differs from the
// description the lead already shows.
function PayeeFact({ change }: { change: ProposedChange }) {
  const { payee } = change
  if (!payee || (!payee.is_new && payee.name === change.description)) return null
  return (
    <ApprovalFact label="Payee">
      {payee.name}
      {payee.is_new && ' · will be created'}
    </ApprovalFact>
  )
}

// The resolved accounts BY NAME (never id). A transfer reads "From → To"; the receiving
// amount is its own row only when it differs from the sent figure the lead shows.
function AccountFacts({ change }: { change: ProposedChange }) {
  const { from, to } = change
  if (from && to) {
    const received =
      from.amount != null &&
      to.amount != null &&
      (to.currency !== from.currency || to.amount !== from.amount)
    return (
      <>
        <ApprovalFact label="Transfer">{`${from.account_name} → ${to.account_name}`}</ApprovalFact>
        {received && (
          <ApprovalFact label="Received">{fCurrency(to.amount!, to.currency || '')}</ApprovalFact>
        )}
      </>
    )
  }
  const leg = from ?? to
  if (!leg) return null
  // An expense or income says its own direction; an investment or adjustment leg does not —
  // a buy and a sell, or +500 and −500, would read the same as a bare "Account".
  const directional = change.txn_kind === 'investment' || change.txn_kind === 'adjustment'
  const label = !directional ? 'Account' : from ? 'From' : 'To'
  return <ApprovalFact label={label}>{leg.account_name}</ApprovalFact>
}

// The historical-rate stamp (ADR 2026-07-09): the base amount and its fx rate, only when
// the leg currency actually differs from base — nothing to add for a same-currency txn.
function BaseFact({ change }: { change: ProposedChange }) {
  const { base } = change
  if (!base || base.amount == null || base.currency === legCurrencyOf(change)) return null
  return (
    <ApprovalFact label={`In ${base.currency || 'base'}`}>
      {fCurrency(base.amount, base.currency || '')}
      {base.fx_rate != null && ` · rate ${base.fx_rate.toFixed(4)}`}
    </ApprovalFact>
  )
}

// Split-category breakdown (a split txn has no single category) behind a disclosure whose
// summary names the categories.
function SplitsDetails({ change }: { change: ProposedChange }) {
  const splits = change.splits
  if (!splits || splits.length === 0) return null
  const labels = splits.map((s) => s.category_label || 'Uncategorized')
  return (
    <ApprovalDetails
      summary={`Split across ${splits.length} categor${splits.length === 1 ? 'y' : 'ies'}: ${labels.join(', ')}`}
    >
      <ApprovalTable
        head={['Category', 'Amount']}
        numeric={[1]}
        rows={splits.map((s, i) => ({
          key: i,
          cells: [labels[i], fAmountNative(s.amount, legCurrencyOf(change) || undefined)],
        }))}
      />
    </ApprovalDetails>
  )
}

function WarningNotes({ warnings }: { warnings: ProposedChange['warnings'] }) {
  return (
    <>
      {(warnings || []).map((w, i) => (
        <ApprovalNote key={i} tone="warn">
          {typeof w === 'string' ? w : w.message}
        </ApprovalNote>
      ))}
    </>
  )
}

// txn_create — a new transaction about to be booked: what it is and its amount, then the
// fields silent resolution decides (category + tier, payee, accounts, base amount).
export function TxnCreateBody({ change }: { change: ProposedChange }) {
  return (
    <>
      <TxnLead change={change} />
      <TxnMeta change={change} />
      <ApprovalFacts>
        <CategoryFact change={change} />
        <PayeeFact change={change} />
        <AccountFacts change={change} />
        <BaseFact change={change} />
      </ApprovalFacts>
      <SplitsDetails change={change} />
      <WarningNotes warnings={change.warnings} />
    </>
  )
}

const diffValue = (v: unknown) => (v == null || v === '' ? '—' : renderGenericValue(v) || '—')

// txn_update — the before → after rows are the primary content; the resolved facts the
// diff does not already cover follow, since that is the state the user is confirming. A
// category change reads its AFTER from the resolution (with its tier), not the raw field.
export function TxnUpdateBody({ change }: { change: ProposedChange }) {
  const fields = change.fields || []
  const changed = new Set(fields.map((f) => f.field))
  const showAccounts = !ACCOUNT_FIELDS.some((f) => changed.has(f))
  return (
    <>
      <TxnLead change={change} />
      <TxnMeta change={change} />
      <ApprovalFacts>
        {fields.map((f) => (
          <ApprovalChange
            key={f.field}
            label={f.label || f.field}
            before={diffValue(f.before)}
            after={
              f.field === 'category'
                ? (categoryText(change) ?? diffValue(f.after))
                : diffValue(f.after)
            }
          />
        ))}
        {!changed.has('category') && <CategoryFact change={change} />}
        {!changed.has('payee_id') && <PayeeFact change={change} />}
        {showAccounts && <AccountFacts change={change} />}
        {!changed.has('base_amount') && <BaseFact change={change} />}
      </ApprovalFacts>
      {fields.length === 0 && <ApprovalNote>No field changes</ApprovalNote>}
      <SplitsDetails change={change} />
      <WarningNotes warnings={change.warnings} />
    </>
  )
}

// Three columns so the amount is never cut off on a phone: the category (and, on a
// multi-account import, the account) sit under the description as muted lines.
function bulkCells(row: BulkRow, withAccount: boolean): ReactNode[] {
  const uncategorized = row.category_source === 'none'
  return [
    fDate(row.date, 'short'),
    <>
      {row.description || '—'}
      <div className={uncategorized ? 'ac-tone-warn' : 'ac-tone-muted'}>
        {uncategorized
          ? row.category_label || 'Uncategorized'
          : `${row.category_label} (${bulkSource(row.category_source)})`}
      </div>
      {withAccount && <div className="ac-tone-muted">{row.account_name}</div>}
    </>,
    row.amount != null ? fCurrency(row.amount, row.currency || '') : '—',
  ]
}

// txn_bulk — ONE card for the whole batch (never one card per row): totals and the rules
// that fired up top, then the capped sample, with the rows it does not show counted.
export function TxnBulkBody({ change }: { change: ProposedChange }) {
  const rows = change.rows || []
  const count = change.count ?? rows.length
  const more = count - rows.length
  const totals = change.totals
  const rulesApplied = change.automations_applied ?? change.rules_applied ?? []
  const uncategorized = rows.filter((r) => r.category_source === 'none').length
  // A statement import is usually one account: say it once as a fact, not on every row.
  // `account_names` covers the whole batch; a card staged before it existed can only trust
  // the sample when every row is shown.
  const sampleAccounts = [...new Set(rows.map((r) => r.account_name))]
  const accounts = change.account_names ?? (more <= 0 ? sampleAccounts : [])
  const oneAccount = accounts.length === 1 ? accounts[0] : null
  return (
    <>
      <ApprovalLead>
        {count} transaction{count === 1 ? '' : 's'}
      </ApprovalLead>
      <ApprovalMeta items={[change.import_description, more > 0 && `+${more} more not shown`]} />
      <ApprovalFacts>
        {totals && totals.inflow_base > 0 && (
          <ApprovalFact label="Money in" tone="good">
            {fCurrency(totals.inflow_base, totals.base_currency)}
          </ApprovalFact>
        )}
        {totals && totals.outflow_base > 0 && (
          <ApprovalFact label="Money out">
            {fCurrency(totals.outflow_base, totals.base_currency)}
          </ApprovalFact>
        )}
        {oneAccount && <ApprovalFact label="Account">{oneAccount}</ApprovalFact>}
        {rulesApplied.length > 0 && (
          <ApprovalFact label="Rules applied">
            {rulesApplied.map((r) => `${r.name} (${r.count})`).join(', ')}
          </ApprovalFact>
        )}
      </ApprovalFacts>
      {uncategorized > 0 && (
        <ApprovalNote tone="warn">
          {uncategorized} of the {rows.length} rows shown are Uncategorized
        </ApprovalNote>
      )}
      <WarningNotes warnings={change.warnings} />
      <ApprovalTable
        head={['Date', 'Transaction', 'Amount']}
        numeric={[2]}
        rows={rows.map((r, i) => ({ key: i, cells: bulkCells(r, !oneAccount) }))}
      />
    </>
  )
}
