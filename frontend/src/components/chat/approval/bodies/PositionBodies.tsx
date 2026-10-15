import type { PositionDisplay, ProposedChange } from '../../../../types/chat'
import { fAmountNative, fCurrency, fDate, fQty } from '../../../../utils/format'
import {
  ApprovalChange,
  ApprovalFact,
  ApprovalFacts,
  ApprovalLead,
  ApprovalMeta,
} from '../ApprovalCard'

/** The position / lot / income confirm card bodies, on the approval-card primitives
 * (docs/APPROVAL_CARDS.md). The card header already names the symbol and venue, so a lead
 * says what the write does and a fact never repeats the header. */

// Fields the backend puts on these cards' `display` beyond the shared PositionDisplay type.
type CardDisplay = PositionDisplay & {
  funding_account_id?: string | null
  funding_account_name?: string | null
  remaining_qty?: number | null
  account_from?: string | null
  account_to?: string | null
  account_to_is_new?: boolean
}

const displayOf = (change: ProposedChange) => (change.display || {}) as CardDisplay

/** A currency-less amount (a lot whose basis currency is unknown) still formats, just
 * without a symbol — never the base currency's, which could be a different unit. */
const money = (n: number, ccy?: string | null) => (ccy ? fCurrency(n, ccy) : fAmountNative(n))

const words = (s: string) => s.replace(/_/g, ' ')
const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

// Position fields an update can change, as the user reads them.
const UPDATE_LABELS: Array<[string, string]> = [
  ['quantity', 'Quantity'],
  ['cost_basis', 'Cost basis'],
  ['cost_currency', 'Cost currency'],
  ['vesting_state', 'Vesting'],
  ['note', 'Note'],
]

function fieldValue(
  field: string,
  side: Record<string, unknown>,
  other: Record<string, unknown>,
): string {
  const v = side[field]
  if (v == null || v === '') return '—'
  if (field === 'quantity') return fQty(v as number)
  if (field === 'cost_basis') {
    return money(Number(v), (side.cost_currency ?? other.cost_currency) as string | undefined)
  }
  return words(String(v))
}

export function PositionUpdateBody({ change }: { change: ProposedChange }) {
  const d = displayOf(change)
  const before = (d.before || {}) as Record<string, unknown>
  const after = (d.after || {}) as Record<string, unknown>
  const edits = UPDATE_LABELS.filter(([f]) => String(before[f] ?? '') !== String(after[f] ?? ''))
  const moves = d.account_to && d.account_to !== d.account_from
  if (edits.length === 0 && !moves)
    return <ApprovalLead tone="muted">No fields change</ApprovalLead>
  const names = [...(moves ? ['account'] : []), ...edits.map(([, label]) => label.toLowerCase())]
  return (
    <>
      <ApprovalLead>Change {names.join(', ')}</ApprovalLead>
      <ApprovalFacts>
        {moves && (
          <ApprovalChange
            label="Account"
            before={d.account_from || '—'}
            after={d.account_to_is_new ? `${d.account_to} (new account)` : d.account_to}
          />
        )}
        {edits.map(([field, label]) => (
          <ApprovalChange
            key={field}
            label={label}
            before={fieldValue(field, before, after)}
            after={fieldValue(field, after, before)}
          />
        ))}
      </ApprovalFacts>
    </>
  )
}

export function PositionAddBody({ change }: { change: ProposedChange }) {
  const d = displayOf(change)
  const ccy = d.cost_currency || d.currency
  const hasBasis = d.cost_basis != null && d.cost_basis !== ''
  const fees = Number(d.fees) || 0
  return (
    <>
      <ApprovalLead>
        Add {fQty(d.quantity)} {d.symbol || d.name}
      </ApprovalLead>
      <ApprovalMeta
        items={[
          d.symbol && d.name,
          d.asset_class,
          d.acquired_at && fDate(d.acquired_at, 'short', 'always'),
          fees > 0 && `${money(fees, ccy)} fees`,
        ]}
      />
      <ApprovalFacts>
        <ApprovalFact
          label="Cost basis"
          tone={hasBasis ? undefined : 'muted'}
          title={hasBasis ? undefined : 'A later sale of this position reports no gain.'}
        >
          {hasBasis ? money(Number(d.cost_basis), ccy) : 'unknown'}
        </ApprovalFact>
      </ApprovalFacts>
    </>
  )
}

export function PositionRemoveBody({ change }: { change: ProposedChange }) {
  const d = displayOf(change)
  return (
    <ApprovalLead figure={d.quantity != null ? `${fQty(d.quantity)} units` : undefined}>
      Remove {d.name || d.symbol}
    </ApprovalLead>
  )
}

const ACQUIRE_VERB: Record<string, string> = {
  buy: 'Buy',
  drp: 'Reinvest',
  transfer_in: 'Transfer in',
  grant: 'Grant',
  opening: 'Opening balance',
}

export function LotAcquireBody({ change }: { change: ProposedChange }) {
  const d = displayOf(change)
  const kind = d.kind || 'buy'
  const qty = Number(d.quantity)
  const costKnown = d.unit_cost != null
  const fees = Number(d.fees) || 0
  const funded = Boolean(d.funding_account_id)
  return (
    <>
      <ApprovalLead
        figure={costKnown ? money(qty * Number(d.unit_cost), d.cost_currency) : undefined}
      >
        {ACQUIRE_VERB[kind] || words(kind)} {fQty(d.quantity)} {d.symbol || d.name}
        {costKnown && ` @ ${money(Number(d.unit_cost), d.cost_currency)}`}
      </ApprovalLead>
      <ApprovalMeta
        items={[
          d.acquired_at && fDate(d.acquired_at, 'short', 'always'),
          fees > 0 && `${money(fees, d.cost_currency)} fees`,
        ]}
      />
      {(!costKnown || kind === 'buy') && (
        <ApprovalFacts>
          {!costKnown && (
            <ApprovalFact
              label="Cost basis"
              tone="muted"
              title="Every later sale of this lot reports no gain."
            >
              unknown
            </ApprovalFact>
          )}
          {kind === 'buy' && (
            <ApprovalFact
              label="Cash"
              tone={funded && costKnown ? undefined : 'warn'}
              title={
                funded && costKnown
                  ? undefined
                  : 'No cash is taken out. Fine if the money already left an account; otherwise net worth rises by the shares with nothing deducted.'
              }
            >
              {funded
                ? costKnown
                  ? `debited from ${d.funding_account_name || 'the funding account'}`
                  : 'not debited'
                : 'not booked'}
            </ApprovalFact>
          )}
        </ApprovalFacts>
      )}
    </>
  )
}

export function LotDeleteBody({ change }: { change: ProposedChange }) {
  const d = displayOf(change)
  const costKnown = d.unit_cost != null
  const qty = Number(d.remaining_qty)
  // The lot's own currency when the card carries it; an older card without one stays a bare
  // number rather than borrowing the base currency's symbol.
  const money = (n: number) => (d.cost_currency ? fCurrency(n, d.cost_currency) : fAmountNative(n))
  return (
    <>
      <ApprovalLead figure={costKnown ? money(qty * Number(d.unit_cost)) : undefined}>
        Delete lot of {fQty(d.remaining_qty)} {d.symbol || d.name}
      </ApprovalLead>
      <ApprovalMeta
        items={[
          d.acquired_at && `Acquired ${fDate(d.acquired_at, 'short', 'always')}`,
          d.kind && words(d.kind),
          costKnown && `${money(Number(d.unit_cost))} per unit`,
        ]}
      />
      {!costKnown && (
        <ApprovalFacts>
          <ApprovalFact label="Cost basis" tone="muted">
            unknown
          </ApprovalFact>
        </ApprovalFacts>
      )}
    </>
  )
}

export function IncomeBody({ change }: { change: ProposedChange }) {
  const d = displayOf(change)
  const ccy = d.currency || undefined
  const tax = Number(d.tax_withheld) || 0
  const showBase =
    d.base_amount != null && d.base_currency && d.base_currency !== (ccy || d.base_currency)
  const kind = sentence(words(d.income_kind || 'dividend'))
  return (
    <>
      <ApprovalLead figure={money(Number(d.net_amount ?? 0), ccy)}>
        {d.is_drp ? `${kind} reinvested, no cash` : `${kind} received`}
      </ApprovalLead>
      <ApprovalMeta items={[d.paid_at && fDate(d.paid_at, 'short', 'always')]} />
      {(tax > 0 || showBase) && (
        <ApprovalFacts>
          {tax > 0 && (
            <ApprovalFact label="Gross">{money(Number(d.gross_amount ?? 0), ccy)}</ApprovalFact>
          )}
          {tax > 0 && <ApprovalFact label="Tax withheld">{money(tax, ccy)}</ApprovalFact>}
          {showBase && (
            <ApprovalFact label={`In ${d.base_currency}`}>
              {money(Number(d.base_amount), d.base_currency)}
            </ApprovalFact>
          )}
        </ApprovalFacts>
      )}
    </>
  )
}
