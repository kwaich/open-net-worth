import type { ProposedChange } from '../../../../types/chat'
import { ApprovalLead, ApprovalNote, ApprovalTable } from '../ApprovalCard'
import { humanizeType, isRecord, plainText } from '../cardTypes'

// An id too long for a narrow column shows its head; the whole id is the hover.
export function BatchCell({ field, value }: { field: string; value: unknown }) {
  if (typeof value === 'string' && /(^|_)id$/.test(field) && value.length > 14) {
    return <span title={value}>{value.slice(0, 10)}…</span>
  }
  const text = plainText(value)
  return text.length > 72 ? <span title={text}>{text.slice(0, 69)}…</span> : <>{text}</>
}

/** The body of every `*_batch` card (`registry.stage_batch`): what the call does and how
 * many, then the rows themselves — they ARE the change, so the table is never folded. */
export function BatchBody({ change }: { change: ProposedChange }) {
  const d = (change.display || {}) as Record<string, unknown>
  const rows = (Array.isArray(d.rows) ? d.rows : []).filter(isRecord)
  const count = typeof d.count === 'number' ? d.count : rows.length
  const columns = [...new Set(rows.flatMap((row) => Object.keys(row)))].filter((key) =>
    rows.some((row) => row[key] != null && row[key] !== ''),
  )

  return (
    <>
      <ApprovalLead>{`${count} ${count === 1 ? 'item' : 'items'}`}</ApprovalLead>
      {columns.length > 0 && (
        <ApprovalTable
          head={columns.map(humanizeType)}
          rows={rows.map((row, i) => ({
            key: i,
            cells: columns.map((key) => <BatchCell key={key} field={key} value={row[key]} />),
          }))}
        />
      )}
      {count > rows.length && <ApprovalNote>{`+${count - rows.length} more`}</ApprovalNote>}
    </>
  )
}
