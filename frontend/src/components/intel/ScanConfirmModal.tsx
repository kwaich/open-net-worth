import { Button, Cluster, Modal, Text } from '../ui'

export function ScanConfirmModal({
  open,
  onCancel,
  onConfirm,
}: {
  open: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <Modal
      open={open}
      onClose={onCancel}
      title="Run intelligence scan?"
      size="sm"
      footer={
        <Cluster justify="end" gap="--space-2">
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="primary" onClick={onConfirm}>
            Scan now
          </Button>
        </Cluster>
      }
    >
      <Text size="sm">
        This scan makes external web-search API calls to fetch news and market signals for your
        watchlist. It counts against your search quota.
      </Text>
    </Modal>
  )
}
