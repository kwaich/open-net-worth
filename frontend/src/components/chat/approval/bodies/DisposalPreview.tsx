import type { DisposalConsumption, ProposedChange } from '../../../../types/chat'
import { fAmountNative, fBase, fCurrency, fDate, fQty, plClass } from '../../../../utils/format'
import {
  ApprovalDetails,
  ApprovalFact,
  ApprovalFacts,
  ApprovalLead,
  ApprovalMeta,
} from '../ApprovalCard'

/** The lot_dispose confirm card body, built from the approval-card primitives
 * (docs/APPROVAL_CARDS.md): what is sold, what it realizes, where the cash goes, and the
 * per-lot breakdown a tap away — its term mix stays in the summary line, since short vs
 * long is the tax fact a user approves on. */
export function DisposalPreview({ change }: { change: ProposedChange }) {
  const d = change.display || {}
  const ccy = d.proceeds_currency || ''
  const transfer = d.is_taxable === false
  const movedTo = transfer ? d.transfer_to_account_name || d.transfer_to_account_id || null : null
  const disposedAt = d.disposed_at || change.disposed_at || ''
  const qty = d.quantity == null ? null : Number(d.quantity)
  const perUnit = d.proceeds_per_unit == null ? null : Number(d.proceeds_per_unit)
  const fees = Number(d.disposal_fees) || 0
  const total =
    d.total_proceeds != null
      ? Number(d.total_proceeds)
      : qty != null && perUnit != null
        ? qty * perUnit
        : null
  const gain = d.realized_gain
  const gainBase = d.realized_gain_base
  const showBase = gainBase != null && ccy && ccy !== (d.base_currency || ccy)

  return (
    <>
      <ApprovalLead figure={!transfer && total != null ? fCurrency(total, ccy) : undefined}>
        {transfer
          ? `${movedTo ? 'Move' : 'Transfer out'} ${fQty(qty)}${movedTo ? ` → ${movedTo}` : ''}`
          : `Sell ${fQty(qty)} @ ${fCurrency(perUnit ?? 0, ccy)}`}
      </ApprovalLead>
      <ApprovalMeta
        items={[
          disposedAt && fDate(disposedAt, 'short', 'always'),
          d.method === 'specific_id' ? 'Specific lots' : 'FIFO',
          fees > 0 && `${fCurrency(fees, ccy)} fees`,
          transfer && 'basis carries over, no gain',
        ]}
      />

      {!transfer && (
        <ApprovalFacts>
          <ApprovalFact
            label="Realized gain"
            tone={gain == null ? 'muted' : gain >= 0 ? 'good' : 'bad'}
          >
            {gain == null
              ? 'unknown — no cost basis'
              : (gain >= 0 ? '+' : '') + fCurrency(gain, ccy)}
            {gain != null && showBase && ` (${(gainBase >= 0 ? '+' : '') + fBase(gainBase)})`}
          </ApprovalFact>
          <ApprovalFact
            label="Cash"
            tone={d.proceeds_account_name ? undefined : 'warn'}
            title={
              d.proceeds_account_name || total == null
                ? undefined
                : `No cash account named. Fine if the money is already in an account; otherwise net worth drops ${fCurrency(total - fees, ccy)}.`
            }
          >
            {d.proceeds_account_name || 'not booked'}
          </ApprovalFact>
        </ApprovalFacts>
      )}

      <LotsConsumed consumptions={d.consumptions || []} ccy={ccy} transfer={transfer} />
    </>
  )
}

function termSummary(consumptions: DisposalConsumption[]): string {
  const n = consumptions.length
  const lots = `${n} lot${n === 1 ? '' : 's'}`
  const count = (t: string) => consumptions.filter((c) => (c.term || 'unknown') === t).length
  const known = (['long', 'short'] as const).filter((t) => count(t) > 0)
  if (known.length === 0) return lots
  const only = known[0]
  if (known.length === 1 && only && count(only) === n) {
    return `${lots} · ${n === 1 ? '' : 'all '}${only}-term`
  }
  return `${lots} · ${known.map((t) => `${count(t)} ${t}-term`).join(', ')}`
}

function LotsConsumed({
  consumptions,
  ccy,
  transfer,
}: {
  consumptions: DisposalConsumption[]
  ccy: string
  transfer: boolean
}) {
  if (consumptions.length === 0) return null
  return (
    <ApprovalDetails summary={termSummary(consumptions)}>
      <table>
        <thead>
          <tr>
            <th>Acquired</th>
            <th>Qty</th>
            <th>Cost</th>
            <th>Term</th>
            {!transfer && <th>Gain{ccy ? ` (${ccy})` : ''}</th>}
          </tr>
        </thead>
        <tbody>
          {consumptions.map((c, i) => (
            <tr key={c.lot_id ?? i}>
              <td>{c.acquired_at ? fDate(c.acquired_at, 'numeric') : '—'}</td>
              <td>{fQty(c.qty)}</td>
              <td>
                {c.unit_cost == null ? '—' : fAmountNative(c.unit_cost, c.cost_currency || ccy)}
              </td>
              <td>
                <span className={`term-badge term-${c.term || 'unknown'}`}>
                  {c.term || 'unknown'}
                </span>
              </td>
              {!transfer && (
                <td className={c.realized_gain == null ? 'muted' : plClass(c.realized_gain)}>
                  {c.realized_gain == null
                    ? '—'
                    : (c.realized_gain >= 0 ? '+' : '') +
                      fAmountNative(c.realized_gain, c.cost_currency || ccy)}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </ApprovalDetails>
  )
}
