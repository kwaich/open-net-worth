import { Modal, Button, Text } from '../ui'

export interface ConfirmSpec {
  title: string
  body: string
  confirmLabel?: string
  onConfirm: () => void
}

export function ConfirmDialog({ spec, onCancel }: { spec: ConfirmSpec; onCancel: () => void }) {
  return (
    <Modal
      open
      onClose={onCancel}
      size="sm"
      title={spec.title}
      footer={
        <div className="intel-pa-footer-right">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="danger" onClick={spec.onConfirm}>
            {spec.confirmLabel ?? 'Delete'}
          </Button>
        </div>
      }
    >
      <Text size="sm" tone="muted">
        {spec.body}
      </Text>
    </Modal>
  )
}
