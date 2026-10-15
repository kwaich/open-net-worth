// Settings › Intelligence → Pulse summaries: how much the Holdings brief and each watchlist tab's
// brief say, from a one-clause headline (1) to a short narrative Lucius writes (5).
import type { BriefVerbosity } from '../../../../intel/monitoringConfig'
import { verbosityOf } from '../../../../intel/monitoringConfig'
import { Field, Panel, Select } from '../../ds'
import type { PanelProps } from './fields'

const VERBOSITY_LABELS: Record<BriefVerbosity, string> = {
  1: '1 · Headline',
  2: '2 · Short',
  3: '3 · Standard',
  4: '4 · Detailed',
  5: '5 · Written by Lucius',
}

const HOLDINGS_SAMPLES: Record<BriefVerbosity, string> = {
  1: '“Your book is +S$120 (+0.4%) today.”',
  2: '“Your book is +S$120 (+0.4%) today — NVDA led (+S$95), AAPL dragged (−S$30).”',
  3: '“… You’re 1.9 pts ahead of SPY 1M.”',
  4: '“… Top movers NVDA, AAPL, VWRA. The month’s biggest driver: NVDA (+S$410).”',
  5: 'Three to five sentences Lucius writes from the same figures, fact-checked. Uses a model call about every 30 minutes while you have the tab open.',
}

const WATCHLIST_SAMPLES: Record<BriefVerbosity, string> = {
  1: '“AMD fell 9.0% today — 3.1× its usual move.”',
  2: '“… NVDA reports earnings in 3 days.”',
  3: '“… Over the past month, AVGO did best against its comparison, INTC worst.”',
  4: '“… Also this week: TSM (ex-dividend). 4 up, 2 down, 1 flagged.”',
  5: 'Three to five sentences Lucius writes from the list’s rows, fact-checked. Uses a model call about every 30 minutes per list.',
}

function VerbositySelect({
  label,
  value,
  onChange,
}: {
  label: string
  value: BriefVerbosity
  onChange: (v: BriefVerbosity) => void
}) {
  return (
    <Select
      aria-label={label}
      value={String(value)}
      onChange={(e) => onChange(Number(e.target.value) as BriefVerbosity)}
    >
      {([1, 2, 3, 4, 5] as BriefVerbosity[]).map((v) => (
        <option key={v} value={v}>
          {VERBOSITY_LABELS[v]}
        </option>
      ))}
    </Select>
  )
}

export function PulseSummaryPanel({ cfg, save }: PanelProps) {
  const holdings = verbosityOf(cfg, 'holdings')
  const watchlist = verbosityOf(cfg, 'watchlist')
  return (
    <Panel
      title="Pulse summaries"
      sub="How much the brief at the top of each Intel tab says. Levels 1–4 are instant; level 5 is written by Lucius."
    >
      <div className="rd-formgrid">
        <Field label="Holdings brief" help={HOLDINGS_SAMPLES[holdings]}>
          <VerbositySelect
            label="Holdings brief detail"
            value={holdings}
            onChange={(v) => save({ holdings_brief_verbosity: v }, 'Holdings brief updated')}
          />
        </Field>
        <Field label="Watchlist briefs" help={WATCHLIST_SAMPLES[watchlist]}>
          <VerbositySelect
            label="Watchlist brief detail"
            value={watchlist}
            onChange={(v) => save({ watchlist_brief_verbosity: v }, 'Watchlist briefs updated')}
          />
        </Field>
      </div>
    </Panel>
  )
}
