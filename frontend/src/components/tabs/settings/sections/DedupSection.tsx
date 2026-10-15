// Settings → Duplicate Detection. Ported from the DedupSection in
// design_handoffs/settings_handoff/prototype/redesign/settings.jsx.
//
// Two panels: the engine's configuration, then the pairs it has already ruled on. The engine
// skips re-flagging what it has decided, so forgetting a decision is how you make it look again.
import { useState } from 'react'
import {
  forgetMatchDecision,
  getDedupConfig,
  listMatchDecisions,
  setDedupConfig,
} from '../../../../api/matches'
import type {
  DedupConfig,
  DedupPolicy,
  MatchDecision,
  MatchDecisionsResponse,
} from '../../../../api/matches'
import { useModels } from '../../../../hooks/useModels'
import { useCachedFetchState } from '../../../../state/queryCache'
import { fDate } from '../../../../utils/format'
import { ModelPicker } from '../../../ui'
import { Btn, Field, Panel, Select, Toggle, row, useSectionBusy } from '../ds'
import type { SectionProps } from '../ds'

const POLICY_LABELS: Record<DedupPolicy, string> = {
  review_all: 'Conservative — ask on ambiguity',
  auto_transfers: 'Balanced — auto-merge transfers',
  auto_high: 'Aggressive — merge likely dupes',
}

const VERDICT_LABELS: Record<MatchDecision['verdict'], string> = {
  same: 'Merged',
  different: 'Not duplicates',
  pair: 'Linked as a transfer',
}

function decisionLine(d: MatchDecision): string {
  const payee = d.payee_label || 'Unlabelled pair'
  const amount = d.amount_bucket ? ' • ' + d.amount_bucket : ''
  const outcome =
    d.verdict === 'same' ? ' — merged' : d.verdict === 'pair' ? ' — linked' : ' — kept both'
  return payee + amount + outcome
}

export function DedupSection({ toast }: SectionProps) {
  const {
    data: cfg,
    initialLoading: loadingCfg,
    refresh: refreshCfg,
  } = useCachedFetchState<DedupConfig>('/api/matches/config', getDedupConfig, { staleTime: 0 })
  useSectionBusy(loadingCfg && !cfg)
  const decisionsKey = '/api/matches/decisions?limit=50'
  const { data: decisions, refresh: refreshDecisions } =
    useCachedFetchState<MatchDecisionsResponse>(decisionsKey, () => listMatchDecisions(50), {
      staleTime: 0,
    })
  const { models, defaultModel } = useModels(true)
  const [busy, setBusy] = useState(false)

  const patch = async (
    p: Partial<Pick<DedupConfig, 'policy' | 'l3_enabled' | 'l3_model' | 'l3_max_per_run'>>,
    message: string,
  ) => {
    setBusy(true)
    try {
      await setDedupConfig(p)
      void refreshCfg()
      toast(message)
    } catch {
      toast('Could not save — try again')
    } finally {
      setBusy(false)
    }
  }

  const forget = async (signature: string) => {
    try {
      await forgetMatchDecision(signature)
      void refreshDecisions()
      toast('Decision forgotten — pair will be re-evaluated')
    } catch {
      toast('Could not forget that decision')
    }
  }

  const policyOptions = (cfg?.policy_options ??
    (Object.keys(POLICY_LABELS) as DedupPolicy[])) as DedupPolicy[]
  const rows = decisions?.decisions ?? []

  return (
    <>
      <Panel
        title="Engine configuration"
        sub="How imported transactions are matched against what's already in the ledger"
      >
        <div className="rd-formgrid">
          <Field
            label="Auto-merge policy"
            help="Conservative asks before merging anything ambiguous"
          >
            <Select
              value={cfg?.policy ?? 'auto_transfers'}
              disabled={busy || !cfg}
              onChange={(e) =>
                void patch({ policy: e.target.value as DedupPolicy }, 'Auto-merge policy updated')
              }
            >
              {policyOptions.map((p) => (
                <option key={p} value={p}>
                  {POLICY_LABELS[p] ?? p}
                </option>
              ))}
            </Select>
          </Field>
          <Field
            label="Judgment model"
            help="Which model rules on a lookalike pair. Cheap models are usually enough."
          >
            <ModelPicker
              label="Judgment model"
              models={models}
              value={cfg?.l3_model ?? ''}
              defaultLabel="Default"
              defaultModelId={defaultModel}
              disabled={busy || !cfg}
              onChange={(next) => void patch({ l3_model: next }, 'Judgment model updated')}
            />
          </Field>
          <Field
            label="Max AI checks per run"
            help="Caps the spend on one import — pairs beyond this are left for you to review."
          >
            <input
              className="rd-input"
              type="number"
              min={0}
              defaultValue={cfg?.l3_max_per_run ?? 0}
              key={cfg?.l3_max_per_run ?? 'na'}
              onBlur={(e) => {
                const next = Number(e.target.value)
                if (!Number.isFinite(next) || next < 0 || next === cfg?.l3_max_per_run) return
                void patch({ l3_max_per_run: next }, 'Check limit updated')
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') e.currentTarget.blur()
              }}
            />
          </Field>
        </div>
        {row(
          'Use AI to judge ambiguous duplicates',
          'Exact-match pairs never need AI; this covers same-day/same-amount lookalikes.',
          <Toggle
            on={cfg?.l3_enabled ?? false}
            disabled={busy || !cfg}
            onChange={(v) =>
              void patch({ l3_enabled: v }, v ? 'AI judgment enabled' : 'AI judgment disabled')
            }
            label="AI judge"
          />,
        )}
      </Panel>

      <Panel
        title="Remembered decisions"
        sub="The engine skips re-flagging pairs it has already decided on. Forget a decision to let the engine re-evaluate that pair."
        flush
      >
        {rows.length ? (
          rows.map((d) => (
            <div className="rd-row" key={d.signature}>
              <div className="rd-row-main">
                <div className="rd-row-sub" style={{ color: 'var(--ink)' }}>
                  {decisionLine(d)}
                </div>
                <div className="rd-row-meta">
                  <span>{VERDICT_LABELS[d.verdict]}</span>
                  <span>{fDate(d.created_at, 'short', 'always')}</span>
                </div>
              </div>
              <div className="rd-row-actions">
                <Btn size="xs" variant="ghost" onClick={() => void forget(d.signature)}>
                  Forget
                </Btn>
              </div>
            </div>
          ))
        ) : (
          <div style={{ padding: '14px 18px' }}>
            <span className="rd-help">
              Nothing decided yet — the engine records a decision the first time it rules on a
              lookalike pair.
            </span>
          </div>
        )}
      </Panel>
    </>
  )
}
