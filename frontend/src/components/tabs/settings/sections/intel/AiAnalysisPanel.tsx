// Settings › Intelligence → AI analysis: the model-written layers on watched tickers (explained
// alerts, Lucius's read on every watch story) and how far their opinion may go.
import { NOT_ADVICE, OPINION_OPTIONS } from '../../../../intel/monitoringConfig'
import type { OpinionLevel } from '../../../../intel/monitoringConfig'
import { Field, Panel, Select, Toggle, row } from '../../ds'
import { NumberInput } from './fields'
import type { PanelProps } from './fields'

export function AiAnalysisPanel({ cfg, save }: PanelProps) {
  const reads = cfg.watch_reads_enabled ?? true
  const level = cfg.ai_opinion_level ?? 'information'
  const chosen = OPINION_OPTIONS.find((o) => o.value === level) ?? OPINION_OPTIONS[0]!

  return (
    <Panel
      title="AI analysis on watched tickers"
      sub="What Lucius writes on top of the price signals. Every figure is checked against the data before it's shown."
    >
      {row(
        'Explained alerts',
        'On a material move, Lucius writes a sourced explanation and sends it to the channels you chose for watch alerts (at most 8 a day).',
        <Toggle
          on={cfg.explained_alerts_enabled ?? true}
          label="Explained alerts"
          onChange={(v) =>
            save(
              { explained_alerts_enabled: v },
              v ? 'Explained alerts on' : 'Explained alerts off',
            )
          }
        />,
      )}
      {row(
        'Lucius’s read on every watch story',
        'A short in-app read on each watch story — what it means for you and what to watch next. Never pushed.',
        <Toggle
          on={reads}
          label="Lucius's read on watch stories"
          onChange={(v) =>
            save({ watch_reads_enabled: v }, v ? 'Watch-story reads on' : 'Watch-story reads off')
          }
        />,
      )}
      <div className="rd-formgrid">
        <Field label="Reads per day" help="A cap on the model calls these reads can make (0–50).">
          <NumberInput
            id="watch-reads-per-day"
            value={cfg.watch_reads_per_day ?? 20}
            min={0}
            max={50}
            disabled={!reads}
            onCommit={(n) => save({ watch_reads_per_day: n }, 'Reads per day updated')}
          />
        </Field>
        <Field label="How far Lucius’s opinion goes" help={`${chosen.help} ${NOT_ADVICE}`}>
          <Select
            aria-label="How far Lucius's opinion goes"
            value={level}
            onChange={(e) =>
              save({ ai_opinion_level: e.target.value as OpinionLevel }, 'Opinion level updated')
            }
          >
            {OPINION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </Select>
        </Field>
      </div>
    </Panel>
  )
}
