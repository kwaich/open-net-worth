// Shared typed prop bag for the commitment detail screen — mirrors the design handoff's
// single `m={...}` object threaded into every sub-component (detail3.jsx), but scoped to ONE
// commitment (built fresh per render by useDetailMutations, see detailMutations.ts) and typed
// instead of `any`. Every write method performs a REAL fetch against /api/commitments/*; there
// is no local-only/scripted state here (see docs/adr/2026-07-22-commitments-tab-redesign-
// faithful-port.md).
import type { Commitment, CommitmentAction } from '../../../types/commitments'

export interface DetailMutations {
  onAskLucius: (text: string) => void
  onOpenCommitment: (id: string) => void
  onOpenTxn: (txnId: string) => void

  // schedule
  updateInstallment: (installmentId: string, changes: Record<string, unknown>) => Promise<void>
  removeInstallment: (installmentId: string) => Promise<void>
  payInstallment: (installmentId: string, amountPaid: number) => Promise<void>
  linkTxn: (installmentId: string, txnId: string) => Promise<void>

  // funding
  updateFunding: (fundingId: string, changes: Record<string, unknown>) => Promise<void>
  removeFunding: (fundingId: string) => Promise<void>
  addFunding: (source: Record<string, unknown>) => Promise<void>

  // actions
  updateAction: (actionId: string, changes: Record<string, unknown>) => Promise<void>
  addAction: (action: Record<string, unknown>) => Promise<void>
  setActionMissed: (actionId: string, fundingId: string | null, missed: boolean) => Promise<void>
  /** Arms/disarms a real, persisted one-shot Lucius Automation for a real (non-derived)
   *  action and stores/clears its id on the action's own reminder_automation_id. */
  setActionReminder: (action: CommitmentAction, on: boolean) => Promise<void>

  // top-level
  rename: (name: string) => Promise<void>
  updateNote: (note: string) => Promise<void>
  setStatus: (status: Commitment['status']) => Promise<void>
  deleteCommitment: () => Promise<void>
}
