// Real write callbacks for the commitment detail screen — every one is a genuine
// PATCH/PUT/DELETE against the existing /api/commitments/* endpoints (see docs/adr/2026-07-22-
// commitments-tab-redesign-faithful-port.md, "Real writes, real endpoints"); none of it is
// scripted/local-only. Bundled by useDetailMutations into the write half of DetailMutations —
// detail.tsx merges in the three navigation callbacks (onAskLucius/onOpenCommitment/onOpenTxn)
// to produce the full bag passed to every tab.
import { useMemo } from 'react'
import { authFetch } from '../../../api/client'
import { createAutomation, deleteAutomation } from '../../../api/automations'
import type { Commitment, CommitmentAction, FundingSource } from '../../../types/commitments'
import { buildPartialPaymentInstallments } from './partialPayment'
import type { DetailMutations } from './detailTypes'

type WriteMutations = Omit<DetailMutations, 'onAskLucius' | 'onOpenCommitment' | 'onOpenTxn'>

/** Every write below awaits this before calling `onChanged()` — a non-ok response (404 if the
 *  row was concurrently deleted/edited elsewhere, 422 on a validation error) throws instead of
 *  silently proceeding as if the write landed. `onChanged()` is what shows the confirming
 *  refetch, so calling it on a failed write would visually contradict itself once the stale
 *  row reappears; a caller that needs to react to the failure can catch this rejection. */
async function assertOk(res: Response): Promise<void> {
  if (!res.ok) {
    let detail = ''
    try {
      detail = await res.text()
    } catch {
      /* body already consumed or unreadable — the status code is still informative */
    }
    throw new Error(`Commitments write failed (${res.status})${detail ? `: ${detail}` : ''}`)
  }
}

const patchJson = async (url: string, body: unknown): Promise<void> =>
  assertOk(await authFetch(url, { method: 'PATCH', body: JSON.stringify(body) }))
const putJson = async (url: string, body: unknown): Promise<void> =>
  assertOk(await authFetch(url, { method: 'PUT', body: JSON.stringify(body) }))

/** Strips the client-only `value_base` hydration field (fundingValue.ts) before writing a
 *  funding_plan back — it's derived (FX rate / live position price), never something the store
 *  should persist; persisting it would silently go stale the moment a price moves. */
function stripDerived(sources: FundingSource[] | undefined): Record<string, unknown>[] {
  return (sources || []).map(({ value_base: _value_base, ...rest }) => rest)
}

export function useDetailMutations(commitment: Commitment, onChanged: () => void): WriteMutations {
  return useMemo(() => {
    const cid = commitment.id
    return {
      updateInstallment: async (iid: string, changes: Record<string, unknown>) => {
        await patchJson(`/api/commitments/${cid}/installments/${iid}`, { changes })
        onChanged()
      },
      removeInstallment: async (iid: string) => {
        const next = commitment.installments.filter((i) => i.id !== iid)
        await putJson(`/api/commitments/${cid}/installments`, { installments: next })
        onChanged()
      },
      payInstallment: async (iid: string, amount: number) => {
        const inst = commitment.installments.find((i) => i.id === iid)
        if (!inst) return
        if (amount >= inst.amount - 0.005) {
          await patchJson(`/api/commitments/${cid}/installments/${iid}`, {
            changes: { status: 'paid' },
          })
        } else {
          const next = buildPartialPaymentInstallments(commitment.installments, iid, amount)
          await putJson(`/api/commitments/${cid}/installments`, { installments: next })
        }
        onChanged()
      },
      linkTxn: async (iid: string, txnId: string) => {
        await patchJson(`/api/commitments/${cid}/installments/${iid}`, {
          changes: { status: 'paid', paid_txn_id: txnId },
        })
        onChanged()
      },

      updateFunding: async (fid: string, changes: Record<string, unknown>) => {
        await patchJson(`/api/commitments/${cid}/funding/${fid}`, { changes })
        onChanged()
      },
      removeFunding: async (fid: string) => {
        const next = stripDerived(commitment.funding_plan).filter((f) => f.id !== fid)
        await putJson(`/api/commitments/${cid}/funding`, { funding_plan: next })
        onChanged()
      },
      addFunding: async (source: Record<string, unknown>) => {
        const next = [...stripDerived(commitment.funding_plan), source]
        await putJson(`/api/commitments/${cid}/funding`, { funding_plan: next })
        onChanged()
      },

      updateAction: async (aid: string, changes: Record<string, unknown>) => {
        await patchJson(`/api/commitments/${cid}/actions/${aid}`, { changes })
        onChanged()
      },
      addAction: async (action: Record<string, unknown>) => {
        const next = [...(commitment.actions || []), action]
        await putJson(`/api/commitments/${cid}/actions`, { actions: next })
        onChanged()
      },
      setActionMissed: async (aid: string, fundingId: string | null, missed: boolean) => {
        await patchJson(`/api/commitments/${cid}/actions/${aid}`, {
          changes: { status: missed ? 'missed' : 'pending' },
        })
        if (fundingId) {
          await patchJson(`/api/commitments/${cid}/funding/${fundingId}`, {
            changes: { disabled: missed },
          })
        }
        onChanged()
      },
      setActionReminder: async (action: CommitmentAction, on: boolean) => {
        if (on) {
          const reminder = `Reminder: "${action.label}" is due ${action.due_date} (${commitment.name}).`
          const automation = await createAutomation({
            name: `Reminder — ${action.label}`,
            description: `Sends the reminder for ${action.label}.`,
            instructions: `Send this message to the user through all registered notification channels:\n\n${reminder}`,
            trigger: { kind: 'schedule', type: 'once', at: `${action.due_date}T09:00:00` },
            permission_mode: 'auto',
            always_allowed: [
              {
                src: 'Open Net Worth',
                desc: `Send the reminder for ${action.label}.`,
                capability: 'notify',
                tools: ['notify'],
              },
            ],
            max_runs: 1,
            status: 'active',
          })
          await patchJson(`/api/commitments/${cid}/actions/${action.id}`, {
            changes: { reminder_automation_id: automation.id },
          })
        } else {
          if (action.reminder_automation_id) {
            await deleteAutomation(action.reminder_automation_id)
          }
          await patchJson(`/api/commitments/${cid}/actions/${action.id}`, {
            changes: { reminder_automation_id: null },
          })
        }
        onChanged()
      },

      rename: async (name: string) => {
        await putJson(`/api/commitments/${cid}`, { changes: { name } })
        onChanged()
      },
      updateNote: async (note: string) => {
        await putJson(`/api/commitments/${cid}`, { changes: { note } })
        onChanged()
      },
      setStatus: async (status: Commitment['status']) => {
        await putJson(`/api/commitments/${cid}`, { changes: { status } })
        onChanged()
      },
      deleteCommitment: async () => {
        await assertOk(await authFetch(`/api/commitments/${cid}`, { method: 'DELETE' }))
        onChanged()
      },
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commitment, onChanged])
}
