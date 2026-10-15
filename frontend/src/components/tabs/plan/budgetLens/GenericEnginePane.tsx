// budgetLens/GenericEnginePane.tsx — the fallback renderer for a budget engine the switcher
// discovered from GET /api/planning/engines that this build has no bespoke UI for (RFC
// Decision D: a "generic"-render engine renders through the host's own shapes). Zero-based
// (custom) and 50/30/20 (its own pane) never land here; anything a community pack adds later
// does, automatically, with no frontend change.
//
// Owns the full setup lifecycle (empty -> draft/blank -> review -> configured), mirroring
// proportional/ProportionalPane.tsx's structure but generalized past 50/30/20's bucket
// concept: a generic engine's setup verb is a plain include/exclude category-id list
// (discovered from the engine's OWN declared verb params via genericRows.ts, never
// hardcoded), so the review is a per-row toggle rather than a bucket picker. Nothing
// engine-specific is hardcoded here — setup copy comes from `descriptor`, and the verb
// id/param name come from `descriptor.verbs`.
import { useMemo, useState } from 'react'
import { ErrorState, Panel, Skeleton } from '../../../ui'
import { useCategories } from '../../../../hooks/serverState'
import { useQueryCache } from '../../../../state/queryCache'
import { fBase, fPct } from '../../../../utils/format'
import { applyBudgetVerb, fetchBudgetView, fetchEngineDraft, viewKey } from './api'
import { currentYm } from './period'
import { GenericEngineSetup } from './GenericEngineSetup'
import { GenericEngineReview } from './GenericEngineReview'
import type { GenericReviewSource } from './GenericEngineReview'
import {
  blankGenericRows,
  listParamName,
  proposedRows,
  proposedSelection,
  setupVerb,
} from './genericRows'
import type { BudgetEngineDescriptor, GenericDraftRow, HeadlineUnit, Measure } from './types'

function formatHeadline(value: number, unit: HeadlineUnit): string {
  if (unit === 'percent') return fPct(value)
  if (unit === 'count') return String(Math.round(value))
  return fBase(value)
}

function formatMeasure(m: Measure): string {
  if (m.amount_base != null) return fBase(m.amount_base)
  if (m.percent != null) return fPct(m.percent)
  return '—'
}

interface ReviewState {
  source: GenericReviewSource
  verbId: string
  paramName: string
  rows: GenericDraftRow[]
  selected: ReadonlySet<string>
}

export function GenericEnginePane({
  descriptor,
  onConfiguredChange,
  onAskLucius,
}: {
  descriptor: BudgetEngineDescriptor
  onConfiguredChange: () => void
  onAskLucius?: (text: string) => void
}) {
  const period = useMemo(() => currentYm(), [])
  const categoriesQuery = useCategories()
  const verb = useMemo(() => setupVerb(descriptor.verbs ?? []), [descriptor.verbs])
  const paramName = listParamName(verb)

  const [review, setReview] = useState<ReviewState | null>(null)
  const [drafting, setDrafting] = useState(false)
  const [draftError, setDraftError] = useState<string | null>(null)
  const [applying, setApplying] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  // Local override so the pane flips to the configured view the INSTANT Apply succeeds,
  // without waiting on the parent's own engines-list round trip (mirrors ProportionalPane).
  const [justConfigured, setJustConfigured] = useState(false)
  const configured = descriptor.configured || justConfigured

  const view = useQueryCache(
    viewKey(descriptor.id, period),
    () => fetchBudgetView(descriptor.id, period),
    {
      enabled: configured && !review,
    },
  )

  async function runDraft() {
    if (!verb || !paramName) return
    setDrafting(true)
    setDraftError(null)
    try {
      const res = await fetchEngineDraft(descriptor.id, period)
      if (!res.has_proposal || !res.proposed_state) {
        setDraftError('Nothing to draft yet — try Start blank instead.')
        return
      }
      setReview({
        source: 'draft',
        verbId: verb.id,
        paramName,
        rows: proposedRows(res.proposed_state),
        selected: proposedSelection(res.proposed_state, paramName),
      })
    } catch (e) {
      setDraftError(e instanceof Error ? e.message : 'Could not draft a proposal — try again.')
    } finally {
      setDrafting(false)
    }
  }

  function startBlank() {
    if (!verb || !paramName) return
    setReview({
      source: 'blank',
      verbId: verb.id,
      paramName,
      rows: blankGenericRows(categoriesQuery.data ?? []),
      selected: new Set(),
    })
  }

  async function apply(selected: ReadonlySet<string>) {
    if (!review) return
    setApplying(true)
    setApplyError(null)
    try {
      await applyBudgetVerb(descriptor.id, review.verbId, {
        [review.paramName]: Array.from(selected),
      })
      setJustConfigured(true)
      setReview(null)
      onConfiguredChange()
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : 'Could not save your selection — try again.')
    } finally {
      setApplying(false)
    }
  }

  if (review) {
    return (
      <GenericEngineReview
        rows={review.rows}
        initialSelected={review.selected}
        source={review.source}
        busy={applying}
        error={applyError}
        onApply={apply}
        onCancel={() => setReview(null)}
      />
    )
  }

  if (!configured) {
    return (
      <>
        <GenericEngineSetup
          descriptor={descriptor}
          drafting={drafting}
          canSetUp={!!verb && !!paramName}
          onDraft={runDraft}
          onStartBlank={startBlank}
          onAskLucius={onAskLucius}
        />
        {draftError && <p className="bgl-review__error">{draftError}</p>}
      </>
    )
  }

  if (view.initialLoading) return <Skeleton width="100%" height={160} radius="var(--radius-lg)" />
  if (view.error && !view.data) {
    return (
      <ErrorState message={`Couldn't load ${descriptor.name}.`} onRetry={() => view.refresh()} />
    )
  }
  if (!view.data) return null

  return (
    <div className="bgl-generic">
      <div className="bgl-generic__headline">
        <span>{view.data.headline.label}</span>
        <strong>{formatHeadline(view.data.headline.value, view.data.headline.unit)}</strong>
      </div>
      {view.data.groups.map((g) => (
        <Panel key={g.id} title={g.label}>
          {g.rows.map((row) => (
            <div key={row.id} className="bgl-generic__row">
              <span>{row.label}</span>
              <span className="money">
                {row.measures.map((m) => `${m.label} ${formatMeasure(m)}`).join(' • ')}
              </span>
            </div>
          ))}
        </Panel>
      ))}
    </div>
  )
}
