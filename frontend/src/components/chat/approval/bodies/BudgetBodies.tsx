import type { PositionDisplay, ProposedChange } from '../../../../types/chat'
import { fBase, fDate } from '../../../../utils/format'
import {
  ApprovalFact,
  ApprovalFacts,
  ApprovalLead,
  ApprovalMeta,
  ApprovalNote,
} from '../ApprovalCard'
import { fBudgetMonth, fmt, permissionArguments, renderGenericValue } from '../cardTypes'
import { GenericChangePreview } from './GenericBodies'

// The envelope / group ids the planning tools stage, and the names the backend resolves beside
// them for the card (`_budget_confirm.with_names`). A card staged before names existed has ids only.
type Display = PositionDisplay & {
  envelope_id?: string
  from_envelope_id?: string
  to_envelope_id?: string
  envelope_name?: string
  from_envelope_name?: string
  to_envelope_name?: string
  group_name?: string
  category_name?: string
  category_names?: string[]
  needs_whole_amount?: boolean | null
}

// A name first; an older card's `category` next; the bare id (or `__rta`, the pool) last.
const envelopeLabel = (name: unknown, id: unknown) =>
  id === '__rta' ? 'Ready to Assign' : fmt(name ?? id)

const amountOf = (d: Display) => (d.amount != null ? fBase(Number(d.amount)) : '—')

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

const TARGET_KINDS: Record<string, string> = { by_date: 'savings' }

// A snooze is a budget month (YYYY-MM); anything else is a date.
const fSnoozeUntil = (v: string) =>
  /^\d{4}-\d{2}$/.test(v) ? fBudgetMonth(v) : fDate(v, 'short', 'always')

function TargetBody({ d }: { d: Display }) {
  const cat = envelopeLabel(d.envelope_name ?? d.category_name ?? d.category, d.envelope_id)
  if (!d.target_type) {
    return (
      <>
        <ApprovalLead>
          {fmt(d.op || 'Clear target')} on {cat}
        </ApprovalLead>
        <ApprovalMeta items={[d.snooze_until && `until ${fSnoozeUntil(d.snooze_until)}`]} />
      </>
    )
  }
  const kind = TARGET_KINDS[d.target_type] ?? d.target_type
  return (
    <>
      <ApprovalLead figure={amountOf(d)}>
        Set {kind} target on {cat}
      </ApprovalLead>
      <ApprovalMeta
        items={[
          d.date && `by ${fDate(d.date, 'short', 'always')}`,
          d.snooze_until && `snoozed until ${fSnoozeUntil(d.snooze_until)}`,
        ]}
      />
      {typeof d.needs_whole_amount === 'boolean' && (
        <ApprovalFacts>
          <ApprovalFact label="Progress counts">
            {d.needs_whole_amount ? 'Set aside' : 'Balance'}
          </ApprovalFact>
        </ApprovalFacts>
      )}
    </>
  )
}

function GroupBody({ change, d }: { change: ProposedChange; d: Display }) {
  const group = fmt(d.group_name ?? d.group_id)
  if (change.type === 'group_create') {
    return <ApprovalLead>{fmt(d.name)}</ApprovalLead>
  }
  if (change.type === 'group_rename') {
    return (
      <ApprovalLead>
        {group}
        {d.name ? ` → ${d.name}` : ''}
      </ApprovalLead>
    )
  }
  if (change.type === 'group_reorder') {
    const order: string[] = d.order || []
    return (
      <>
        <ApprovalLead>Reorder {plural(order.length, 'group', 'groups')}</ApprovalLead>
        <ApprovalMeta items={[order.join(' → ')]} />
      </>
    )
  }
  return (
    <>
      <ApprovalLead>{group}</ApprovalLead>
      <ApprovalNote>Its envelopes move to Others</ApprovalNote>
    </>
  )
}

/** Shared body for all Planning / YNAB envelope budget ops, each on the approval-card
 * primitives (docs/APPROVAL_CARDS.md): a plain-words lead with the amount as its figure,
 * the month or the new order as the muted line. */
export function BudgetChangePreview({ change }: { change: ProposedChange }) {
  const d: Display = change.display || {}
  const month = fBudgetMonth(d.month)

  if (change.type === 'assign') {
    return (
      <>
        <ApprovalLead figure={amountOf(d)}>
          Assign to {envelopeLabel(d.envelope_name ?? d.category_name ?? d.category, d.envelope_id)}
        </ApprovalLead>
        <ApprovalMeta items={[month]} />
      </>
    )
  }

  if (change.type === 'move_money') {
    const from = envelopeLabel(d.from_envelope_name ?? d.from_category, d.from_envelope_id)
    const to = envelopeLabel(d.to_envelope_name ?? d.to_category, d.to_envelope_id)
    return (
      <>
        <ApprovalLead figure={amountOf(d)}>
          Move from {from} → {to}
        </ApprovalLead>
        <ApprovalMeta items={[month]} />
      </>
    )
  }

  if (change.type === 'set_target') return <TargetBody d={d} />

  if (change.type === 'move_category') {
    return (
      <ApprovalLead>
        Move {envelopeLabel(d.envelope_name ?? d.category_name ?? d.category, d.envelope_id)} into{' '}
        {fmt(d.group_name ?? d.group_id)}
      </ApprovalLead>
    )
  }

  if (change.type === 'reorder_categories') {
    const cats: string[] = d.category_names ?? d.categories ?? []
    return (
      <>
        <ApprovalLead>
          Reorder {plural(cats.length, 'category', 'categories')} in{' '}
          {fmt(d.group_name ?? d.group_id)}
        </ApprovalLead>
        <ApprovalMeta items={[cats.join(' → ')]} />
      </>
    )
  }

  if (change.type.startsWith('group_')) return <GroupBody change={change} d={d} />

  return <GenericChangePreview change={change} />
}

const sentenceCase = (key: string) => {
  const words = key.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** The tool-permission body for apply_budget_operation / set_home_budget_engine, read off the
 * SAME `display.arguments` the generic card carries. The header already names the operation
 * and the method, so the body is only what the header can't say: the params, or the one
 * reassurance a lens switch needs. */
export function BudgetOperationChangePreview({ change }: { change: ProposedChange }) {
  if (change.grant_tool === 'set_home_budget_engine') {
    return <ApprovalNote>Other methods stay untouched; switching back loses nothing</ApprovalNote>
  }

  const args = permissionArguments(change)
  const params =
    args.params != null && typeof args.params === 'object' && !Array.isArray(args.params)
      ? (args.params as Record<string, unknown>)
      : {}
  const entries = Object.entries(params).filter(([, v]) => v != null && v !== '')
  if (entries.length === 0) return <ApprovalNote>No parameters.</ApprovalNote>

  return (
    <ApprovalFacts>
      {entries.map(([k, v]) => (
        <ApprovalFact key={k} label={sentenceCase(k)}>
          {renderGenericValue(v)}
        </ApprovalFact>
      ))}
    </ApprovalFacts>
  )
}
