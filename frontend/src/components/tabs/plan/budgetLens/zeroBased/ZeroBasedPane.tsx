// budgetLens/zeroBased/ZeroBasedPane.tsx — the home budget (<BudgetTab>) plus, for a book that
// has not started yet, a setup flow.
//
// Zero-Based had NO setup experience at all: `configured` was hardcoded true and `draft()`
// returned None, so a first-time user picking the most recognisable budgeting method landed in
// an empty table with nothing to react to — the only method in the product that helped them
// least (owner, 2026-08-23).
//
// What it sets up is TARGETS, not assignments. A target says what an envelope needs each
// month, which is what makes "underfunded" mean anything and what gives the first assign-run
// something to aim at. Assigning the money is the user's own ritual and is never done for them
// — that ritual IS zero-based budgeting.
//
// <BudgetTab> renders behind the prompt either way: an empty budget table is the thing being
// set up, so seeing it while you decide is the point.
import { useMemo, useState } from 'react'
import { BudgetTab } from '../../budget'
import type { RegisterFilter } from '../../budget'
import { applyBudgetVerb, fetchEngineDraft } from '../api'
import { currentYm } from '../period'
import { MethodExplainer } from '../MethodExplainer'
import { AmountReview } from '../setup/AmountReview'
import { SetupModal, SetupPrompt } from '../setup/SetupFlow'
import { SetupChooser } from '../setup/SetupChooser'
import { proposedAmountRows } from '../setup/amountRows'
import type { BudgetEngineDescriptor, EnvelopeDraftRow } from '../types'

const SET_TARGETS = 'set_targets'

const COPY = {
  prompt: 'What does each envelope need every month?',
  toggleLabel: (label: string) => `Give ${label} a monthly target`,
  amountLabel: (label: string) => `${label} monthly target`,
  unit: 'targets',
  honesty:
    'Drafted from your recent spending — each target is the median of what you actually spent, ' +
    'not something Lucius reviewed. Check anything that looks off before applying.',
  lowConfidence: 'check amount',
}

export function ZeroBasedPane({
  descriptor,
  onConfiguredChange,
  onAskLucius,
  toast,
  onOpenTxn,
  onOpenRegister,
  categoriesVersion,
}: {
  descriptor: BudgetEngineDescriptor
  onConfiguredChange: () => void
  onAskLucius: (text: string) => void
  toast: (msg: string) => void
  onOpenTxn?: (raw: Record<string, unknown>) => void
  onOpenRegister?: (filter: RegisterFilter) => void
  categoriesVersion?: number
}) {
  const period = useMemo(() => currentYm(), [])
  const [open, setOpen] = useState(false)
  const [rows, setRows] = useState<EnvelopeDraftRow[] | null>(null)
  const [preselected, setPreselected] = useState(true)
  const [drafting, setDrafting] = useState(false)
  const [draftError, setDraftError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [justConfigured, setJustConfigured] = useState(false)
  const configured = descriptor.configured || justConfigured

  async function loadRows(withSelection: boolean) {
    setDrafting(true)
    setDraftError(null)
    try {
      const res = await fetchEngineDraft(descriptor.id, period)
      const drafted = proposedAmountRows(res.proposed_state)
      if (!drafted.length) {
        setDraftError('No envelopes to target yet — create one in your budget first.')
        return
      }
      setPreselected(withSelection)
      setRows(drafted)
    } catch (e) {
      setDraftError(e instanceof Error ? e.message : 'Could not read your spending — try again.')
    } finally {
      setDrafting(false)
    }
  }

  async function apply(targets: { envelope_id: string; amount: number }[]) {
    setApplying(true)
    setApplyError(null)
    try {
      const res = (await applyBudgetVerb(descriptor.id, SET_TARGETS, { targets })) as {
        applied?: number
        failed?: { envelope_id: string | null }[]
      }
      setJustConfigured(true)
      closeSetup()
      onConfiguredChange()
      // The verb reports per-row failures instead of raising, so a partial apply must be said
      // out loud rather than looking like a clean success.
      const failed = res?.failed?.length ?? 0
      toast(
        failed > 0
          ? `Set ${res.applied ?? 0} targets — ${failed} couldn't be saved.`
          : `Set ${res.applied ?? targets.length} monthly targets. Now assign your money.`,
      )
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : 'Could not save your targets — try again.')
    } finally {
      setApplying(false)
    }
  }

  function closeSetup() {
    setOpen(false)
    setRows(null)
    setDraftError(null)
    setApplyError(null)
  }

  return (
    <>
      {!configured && (
        <SetupPrompt
          name={descriptor.name}
          description="Give every envelope a monthly target first — then assign your money against them."
          onSetUp={() => setOpen(true)}
        />
      )}
      <BudgetTab
        toast={toast}
        onOpenTxn={onOpenTxn}
        onOpenRegister={onOpenRegister}
        categoriesVersion={categoriesVersion}
      />
      {/* Unlike a lens with nothing to show yet, zero-based HAS content behind it (the budget table), so
          the pane keeps a compact prompt and both steps run in the modal. */}
      <SetupModal
        open={open || !!rows}
        title={`Set up ${descriptor.name}`}
        subtitle={rows ? undefined : descriptor.description}
        onClose={closeSetup}
      >
        {rows ? (
          <AmountReview
            rows={rows}
            copy={COPY}
            preselected={preselected}
            busy={applying}
            error={applyError}
            onApply={apply}
            onCancel={closeSetup}
          />
        ) : (
          <SetupChooser
            name={descriptor.name}
            busy={drafting}
            error={draftError}
            explainer={<MethodExplainer engineId={descriptor.id} />}
            onDraft={() => loadRows(true)}
            onStartBlank={() => loadRows(false)}
            onAskLucius={() =>
              onAskLucius(
                'Help me set up my zero-based budget — look at my spending history, propose a ' +
                  'monthly target for each envelope, and explain why you chose it.',
              )
            }
          />
        )}
      </SetupModal>
    </>
  )
}
