import { useState } from 'react'
import { renameVaultItem, type VaultItem } from '../../api/vault'
import { refreshVault } from '../../hooks/useVault'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { TextField } from '../ui/TextField'

const HELP: Partial<Record<VaultItem['kind'], string>> = {
  file: 'Leave it empty to use the file name.',
  statement: 'Leave it empty to go back to the account and month.',
  receipt: 'Leave it empty to go back to its transaction’s name.',
  asset: 'The asset’s page shows this name too.',
}
// These fall back to a name of their own when cleared; an artifact or asset file needs one.
const CLEARABLE = new Set<VaultItem['kind']>(['file', 'statement', 'receipt'])

export function RenameDialog({ item, onClose }: { item: VaultItem; onClose: () => void }) {
  const [name, setName] = useState(item.title)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const next = name.trim()
  const blocked = !next && !CLEARABLE.has(item.kind)
  const save = async () => {
    if (blocked) return
    if (next === item.title) return onClose()
    setSaving(true)
    setError(null)
    try {
      await renameVaultItem(item.id, next || null)
      refreshVault()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setSaving(false)
    }
  }
  return (
    <Modal
      open
      onClose={onClose}
      title="Rename"
      size="sm"
      footer={
        <div className="ui-confirm-actions">
          <Button size="sm" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            loading={saving}
            disabled={blocked}
            onClick={() => void save()}
          >
            Save
          </Button>
        </div>
      }
    >
      <form
        className="vault-rename"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <TextField
          aria-label="Name"
          value={name}
          maxLength={160}
          // Not a person's name. iOS ignores autocomplete="off" for its "AutoFill Contact" bar
          // (it offered the owner's own name here) but never offers it on a search-type field.
          type="search"
          autoComplete="off"
          enterKeyHint="done"
          data-autofocus
          onFocus={(e) => e.currentTarget.select()}
          onChange={(e) => setName(e.target.value)}
        />
        {HELP[item.kind] && <p className="vault-rename-help">{HELP[item.kind]}</p>}
        {error && <p className="vault-error">{error}</p>}
      </form>
    </Modal>
  )
}
