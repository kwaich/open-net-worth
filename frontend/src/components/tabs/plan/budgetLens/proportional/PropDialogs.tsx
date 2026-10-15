// budgetLens/proportional/PropDialogs.tsx — the confirm steps of the 50/30/20 table's envelope
// writes, and the New envelope form. Every one is Zero-Based's OWN dialog (budgetEnvelopeOps.tsx)
// — envelopes are shared between the two methods — told what 50/30/20 keeps in place of
// Zero-Based's assigned money: a cap and a share (owner, 2026-09-25).
import { useState } from 'react'
import {
  DeleteEnvelopeModal,
  MergeEnvelopeModal,
  NewEnvelopeModal,
  RemoveCategoryModal,
} from '../../budgetEnvelopeOps'
import type { EnvelopeGroupRef } from '../../budgetEnvelopeOps'
import type { CategoryMeta } from '../../../../../hooks/useBudget'
import {
  addCategory,
  createEnvelope,
  deleteEnvelope,
  removeCategory,
  undoDeleteEnvelope,
} from './envelopeOps'
import { mergeEnvelopes } from './propEdits'
import { fmt } from './propMath'
import type { PropEnvelope, PropMonth } from './propApi'

export type PropDialog =
  | { kind: 'delete'; env: PropEnvelope }
  | { kind: 'merge'; env: PropEnvelope }
  | { kind: 'remove'; env: PropEnvelope; categoryId: string }
  /** From a group's + (or its ⋮ → Add envelope) it lands in that group; else in Others. */
  | { kind: 'new'; groupId?: string; groupName?: string }

const ref = (e: PropEnvelope) => ({ envelopeId: e.id, label: e.label, categoryIds: e.category_ids })

export function PropDialogs({
  dialog,
  m,
  ym,
  monthLabel,
  capsLocked,
  categories,
  hiddenKeys,
  catLabel,
  toast,
  captureUndo,
  onClose,
}: {
  dialog: PropDialog | null
  m: PropMonth
  ym: string
  /** "Sep 2026" — when a merged cap starts counting. */
  monthLabel: string
  capsLocked: boolean
  categories: CategoryMeta[]
  hiddenKeys: Set<string>
  catLabel: (k: string) => string
  toast: (msg: string) => void
  captureUndo: (label: string, inverse: () => Promise<void>) => void
  onClose: () => void
}) {
  const [busy, setBusy] = useState(false)
  if (!dialog) return null
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    try {
      await fn()
      onClose()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Something went wrong — try again.')
    } finally {
      setBusy(false)
    }
  }

  if (dialog.kind === 'new')
    return (
      <NewEnvelopeModal
        groupName={dialog.groupName}
        allGroups={[{ cats: m.envelopes.map(ref) }]}
        categories={categories}
        hiddenKeys={hiddenKeys}
        rowCount={m.envelopes.length}
        busy={busy}
        onCancel={onClose}
        onConfirm={(name, color, categoryIds) =>
          void run(async () => {
            const id = await createEnvelope(name, color, dialog.groupId)
            for (const c of categoryIds) await addCategory(id, c)
            toast(`“${name}” is in ${dialog.groupName ?? 'Others'}`)
          })
        }
      />
    )

  const { env } = dialog
  if (dialog.kind === 'delete')
    return (
      <DeleteEnvelopeModal
        envelopeId={env.id}
        month={ym}
        envelopeName={env.label}
        categoryLabels={env.category_ids.map(catLabel)}
        notes={
          env.cap
            ? [`Its ${fmt(env.cap)} cap stops counting toward ${env.group || 'its group'}`]
            : []
        }
        busy={busy}
        onCancel={onClose}
        onConfirm={() =>
          void run(async () => {
            const { undoToken, deltaRta } = await deleteEnvelope(env.id, ym)
            toast(
              `Envelope “${env.label}” deleted` +
                (deltaRta > 0.005 ? ` — ${fmt(deltaRta)} returned to Ready to Assign` : ''),
            )
            captureUndo('Envelope deleted', () => undoDeleteEnvelope(undoToken))
          })
        }
      />
    )

  if (dialog.kind === 'merge') {
    // The merge target is picked from the book's own groups — the same ones Zero-Based shows.
    const groups: EnvelopeGroupRef[] = m.groups.map((g) => ({
      name: g.name,
      cats: m.envelopes.filter((e) => e.group_id === g.id).map(ref),
    }))
    return (
      <MergeEnvelopeModal
        source={ref(env)}
        allGroups={groups}
        moneyLine={(dest) =>
          env.cap && !capsLocked ? (
            <>
              Its <b>{fmt(env.cap)}</b> cap adds to {dest}&rsquo;s, from {monthLabel} on
            </>
          ) : (
            <>{dest} keeps its own cap</>
          )
        }
        onCancel={onClose}
        onConfirm={(targetId, targetName) => {
          const to = m.envelopes.find((e) => e.id === targetId)
          if (!to) return
          void run(async () => {
            await mergeEnvelopes(env, to, { ym, capsLocked })
            toast(`Merged “${env.label}” into “${targetName}”`)
          })
        }}
      />
    )
  }

  const label = catLabel(dialog.categoryId)
  return (
    <RemoveCategoryModal
      envelopeName={env.label}
      categoryLabel={label}
      sole={env.category_ids.length === 1}
      whereLine={
        <>
          {label}&rsquo;s spending will then be in no envelope. It reduces your savings until you
          put it in one
        </>
      }
      keepsLine={env.cap ? `${env.label} keeps its ${fmt(env.cap)} cap` : `${env.label} stays put`}
      onCancel={onClose}
      onConfirm={() =>
        void run(async () => {
          await removeCategory(env.id, dialog.categoryId)
          toast(`${label} removed from ${env.label}`)
        })
      }
    />
  )
}
