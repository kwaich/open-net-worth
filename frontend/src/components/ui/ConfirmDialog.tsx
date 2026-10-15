// ConfirmDialog — a size="sm" Modal shaped for a single yes/no decision (Cancel vs. a
// primary/danger confirm button). Promoted from PayeesModal's own local PMConfirmDialog (same
// shape, same accounts.css styling) into a shared ui/ primitive the first time a second caller
// needed it — see the class comment in index.css. Use this instead of window.confirm() when a
// decision deserves the app's own styling rather than the browser's native dialog.

import { Modal } from './Modal'
import { Button } from './Button'

export interface ConfirmDialogData {
  title: string
  body: React.ReactNode
  confirmLabel?: string
  cancelLabel?: string
  /** Renders the confirm button as `danger` (e.g. delete) instead of `primary`. */
  danger?: boolean
  /** Blocks confirming while the body is still loading, or has failed to load, the figure the
   *  decision rests on — Cancel always stays live. */
  confirmDisabled?: boolean
}

export function ConfirmDialog({
  data,
  onConfirm,
  onCancel,
}: {
  /** `null` renders nothing — callers hold this as `useState<ConfirmDialogData | null>(null)`. */
  data: ConfirmDialogData | null
  onConfirm: () => void
  onCancel: () => void
}) {
  if (!data) return null
  return (
    <Modal
      open
      onClose={onCancel}
      title={data.title}
      size="sm"
      footer={
        <div className="ui-confirm-actions">
          {/* Opens on the safe choice — never the ✕, never the destructive button. */}
          <Button size="sm" variant="ghost" data-autofocus onClick={onCancel}>
            {data.cancelLabel ?? 'Cancel'}
          </Button>
          <Button
            size="sm"
            variant={data.danger ? 'danger' : 'primary'}
            disabled={data.confirmDisabled}
            onClick={onConfirm}
          >
            {data.confirmLabel ?? 'Confirm'}
          </Button>
        </div>
      }
    >
      <div className="ui-confirm-body">{data.body}</div>
    </Modal>
  )
}
