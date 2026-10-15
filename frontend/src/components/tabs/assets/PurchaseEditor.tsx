// Purchase (a policy: Premiums paid) — what was paid and when. It IS the create wizard's step 4
// (PurchaseStep) in a dialog, and saves exactly {cost, cost_date}; a blank field clears it
// (RFC §3.4). The purchase can't be dated after the first recorded value, so the date picker
// stops there — and the server says why if a typed date is refused.
import type { OwnedAsset } from '../../../types/assets'
import { localIso } from '../../../utils/format'
import { Modal, Stack } from '../../ui'
import * as api from './api'
import { purchaseCopy } from './assetCategoryRules'
import { errorKeysOf, purchaseErrors, purchaseFromAsset, type PurchaseValue } from './assetForms'
import { firstValuedOn } from './assetsData'
import { EditorFooter, SaveError, useEditorSave } from './editorKit'
import { PurchaseStep } from './PurchaseStep'
import { useSyncedDraft } from './useSyncedDraft'

const FIELDS = ['cost', 'cost_date'] as const

export interface PurchaseEditorProps {
  a: OwnedAsset
  onClose(): void
  onSaved(message: string): void
}

export function PurchaseEditor({ a, onClose, onSaved }: PurchaseEditorProps) {
  const today = localIso()
  const firstValue = firstValuedOn(a)
  const maxDate = firstValue && firstValue < today ? firstValue : today
  // Every change merges into the newest value (`latest`), never a copy from the last render.
  const [value, update, latest] = useSyncedDraft<PurchaseValue>(() => purchaseFromAsset(a))
  const { saving, save, check, errorFor, edited, unreadable, dialogError } = useEditorSave()

  async function onSave() {
    const { cost, costDate } = latest.current
    if (!check(purchaseErrors(latest.current, today))) return
    const changes = { cost, cost_date: costDate || null }
    if (await save(() => api.updateAsset(a.id, changes))) onSaved('Purchase updated.')
  }

  return (
    <Modal
      className="as-modal"
      open
      onClose={onClose}
      title={purchaseCopy(a.category).cardTitle}
      subtitle={a.name}
      size="sm"
      footer={
        <EditorFooter saving={saving} label="Save changes" onCancel={onClose} onSave={onSave} />
      }
    >
      <Stack gap="--space-6">
        <PurchaseStep
          category={a.category}
          value={value}
          onChange={(patch) => {
            update(patch)
            edited(...errorKeysOf(Object.keys(patch)))
          }}
          currency={a.currency}
          maxDate={maxDate}
          editing
          onUnreadable={unreadable}
          errors={{ cost: errorFor('cost'), cost_date: errorFor('cost_date') }}
        />
        <SaveError message={dialogError(FIELDS)} />
      </Stack>
    </Modal>
  )
}
