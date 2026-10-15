import { useState } from 'react'
import { removeVaultItem, type VaultItem } from '../../api/vault'
import { refreshVault } from '../../hooks/useVault'
import { ConfirmDialog } from '../ui/ConfirmDialog'
import { NoteDialog } from './NoteDialog'
import { RenameDialog } from './RenameDialog'
import { TagsDialog } from './TagsDialog'
import type { RemovalAction } from './vaultFormat'

export type ItemAction = 'rename' | 'note' | 'tags' | RemovalAction

/** The dialog behind one action on an item — the same from its page's Manage and from its
 *  row's ⋯ menu, so the two can never drift. */
export function ItemActionDialog({
  item,
  action,
  onClose,
  onRemoved,
}: {
  item: VaultItem
  action: ItemAction
  onClose: () => void
  onRemoved?: () => void
}) {
  if (action === 'rename') return <RenameDialog item={item} onClose={onClose} />
  if (action === 'note') return <NoteDialog item={item} onClose={onClose} />
  if (action === 'tags') return <TagsDialog item={item} onClose={onClose} />
  return <RemoveConfirm item={item} action={action} onClose={onClose} onRemoved={onRemoved} />
}

/** A removal, confirmed first; a refusal stays in the confirm, saying why. */
function RemoveConfirm({
  item,
  action,
  onClose,
  onRemoved,
}: {
  item: VaultItem
  action: RemovalAction
  onClose: () => void
  onRemoved?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await removeVaultItem(item.id, { deleteFile: action.deleteFile })
      refreshVault()
      onClose()
      onRemoved?.()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }
  return (
    <ConfirmDialog
      data={{
        title: `${action.label}?`,
        body: (
          <>
            {action.body}
            {error && <p className="vault-error">{error}</p>}
          </>
        ),
        confirmLabel: action.label,
        danger: !action.keeps,
        confirmDisabled: busy,
      }}
      onConfirm={() => void confirm()}
      onCancel={onClose}
    />
  )
}
