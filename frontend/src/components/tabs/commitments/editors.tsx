// Shared inline row editor (detail3.jsx's `EditRow`) — used by both the Schedule tab's
// installment rows and the Funding tab's source rows.
import { DateField } from '../../ui'

export interface EditDraft {
  label: string
  amount: number | string
  due_date?: string
  description?: string
  note?: string
}

interface EditRowProps {
  draft: EditDraft
  setDraft: (d: EditDraft) => void
  onSave: () => void
  onCancel: () => void
  onDelete?: () => void
  withDate?: boolean
  descKey?: 'description' | 'note'
  descPlaceholder?: string
  ccy?: string
}

export function EditRow({
  draft,
  setDraft,
  onSave,
  onCancel,
  onDelete,
  withDate,
  descKey,
  descPlaceholder,
  ccy,
}: EditRowProps) {
  return (
    <div className="c2-edit">
      <div className="c2-edit-row">
        <input
          className="c2-input grow"
          type="text"
          value={draft.label}
          placeholder="Label"
          onChange={(e) => setDraft({ ...draft, label: e.target.value })}
        />
        {withDate && (
          <DateField
            unstyled
            className="c2-input date"
            value={draft.due_date || ''}
            onChange={(e) => setDraft({ ...draft, due_date: e.target.value })}
          />
        )}
        <input
          className="c2-input amt"
          type="number"
          value={draft.amount}
          placeholder={ccy ? `Amount (${ccy})` : 'Amount'}
          onChange={(e) => setDraft({ ...draft, amount: e.target.value })}
        />
      </div>
      {descKey && (
        <textarea
          className="c2-input c2-edit-desc"
          value={draft[descKey] || ''}
          placeholder={descPlaceholder || 'Description (optional)'}
          onChange={(e) => setDraft({ ...draft, [descKey]: e.target.value })}
        />
      )}
      <div className="c2-edit-btns">
        {onDelete && (
          <button className="c2-mini-btn danger" onClick={onDelete}>
            Delete
          </button>
        )}
        <button className="c2-mini-btn" onClick={onCancel}>
          Cancel
        </button>
        <button className="c2-mini-btn pri" onClick={onSave}>
          Save
        </button>
      </div>
    </div>
  )
}
