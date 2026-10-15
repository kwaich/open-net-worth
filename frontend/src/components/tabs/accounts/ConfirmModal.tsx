/**
 * ConfirmModal.tsx — Simple confirmation dialog.
 * Ported from prototype/accounts/modals.jsx ConfirmModal.
 */

import { Modal } from '../../ui/Modal'
import { Button } from '../../ui/Button'
import { Text } from '../../ui/Text'

export interface ConfirmModalProps {
  open: boolean
  title: string
  body: string
  confirmLabel: string
  danger?: boolean
  onConfirm: () => void
  onClose: () => void
}

export function ConfirmModal({
  open,
  title,
  body,
  confirmLabel,
  danger,
  onConfirm,
  onClose,
}: ConfirmModalProps) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      size="sm"
      title={title}
      footer={
        <>
          <Button size="sm" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" variant={danger ? 'danger' : 'primary'} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <Text size="sm" tone="muted">
        {body}
      </Text>
    </Modal>
  )
}
