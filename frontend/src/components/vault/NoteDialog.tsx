import { useRef, useState } from 'react'
import { setVaultNote, type VaultItem } from '../../api/vault'
import { useCaretAtEnd } from '../../hooks/useCaretAtEnd'
import { refreshVault } from '../../hooks/useVault'
import { Button } from '../ui/Button'
import { Modal } from '../ui/Modal'
import { TextArea } from '../ui/TextArea'

const MAX_NOTE = 1000 // services/vault.py MAX_NOTE

/** One note per item, in the user's own words. Saving it empty removes it. */
export function NoteDialog({ item, onClose }: { item: VaultItem; onClose: () => void }) {
  const [text, setText] = useState(item.note ?? '')
  const [saving, setSaving] = useState<'save' | 'remove' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const caretAtEnd = useCaretAtEnd()
  // A held Cmd/Ctrl+Enter repeats faster than `saving` re-renders.
  const busy = useRef(false)
  const write = async (next: string | null, action: 'save' | 'remove') => {
    if (busy.current) return
    if (next === (item.note ?? null)) return onClose()
    busy.current = true
    setSaving(action)
    setError(null)
    try {
      await setVaultNote(item.id, next)
      refreshVault()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      setSaving(null)
      busy.current = false
    }
  }
  const save = () => void write(text.trim() || null, 'save')
  return (
    <Modal
      open
      onClose={onClose}
      title={item.note ? 'Edit note' : 'Add a note'}
      size="sm"
      footer={
        <div className="ui-confirm-actions">
          {item.note && (
            <Button
              size="sm"
              variant="ghost"
              className="vault-note-remove"
              loading={saving === 'remove'}
              disabled={saving === 'save'}
              onClick={() => void write(null, 'remove')}
            >
              Remove
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="primary"
            loading={saving === 'save'}
            disabled={saving === 'remove'}
            onClick={save}
          >
            Save
          </Button>
        </div>
      }
    >
      <div className="vault-rename">
        <TextArea
          aria-label="Note"
          rows={5}
          value={text}
          maxLength={MAX_NOTE}
          placeholder="e.g. Signed copy. The original is in the safe."
          data-autofocus
          onFocus={caretAtEnd}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) save()
          }}
        />
        <p className="vault-rename-help">
          {item.private
            ? 'Lucius can read this note. Private hides only what’s in the file.'
            : 'Lucius reads your note when you ask about this file.'}
        </p>
        {error && <p className="vault-error">{error}</p>}
      </div>
    </Modal>
  )
}
