// Settings › Intelligence → Advanced: the thresholds behind crash-watch and news-spike alerts.
// Collapsed by default — the defaults are calibrated per asset class.
import { useState } from 'react'
import type { CrashThresholds } from '../../../../intel/monitoringConfig'
import { DEFAULT_CRASH } from '../../../../intel/monitoringConfig'
import { Btn, Field, Panel } from '../../ds'
import { NumberInput } from './fields'
import type { PanelProps } from './fields'

const CRASH_FIELDS: { key: keyof CrashThresholds; label: string }[] = [
  { key: 'equity', label: 'Equity crash threshold (%)' },
  { key: 'commodity', label: 'Commodity crash threshold (%)' },
  { key: 'crypto', label: 'Crypto crash threshold (%)' },
]

export function AdvancedPanel({ cfg, save }: PanelProps) {
  const [open, setOpen] = useState(false)
  const active = cfg.enabled
  const ct = cfg.crash_thresholds ?? DEFAULT_CRASH

  return (
    <Panel
      title="Advanced"
      sub="Tune the thresholds that trigger crash-watch and news-spike alerts. Only change these if the defaults are too noisy or too quiet."
      actions={
        <Btn size="xs" variant="ghost" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          {open ? 'Hide' : 'Show'}
        </Btn>
      }
    >
      {open ? (
        <div className="rd-formgrid">
          {CRASH_FIELDS.map((f) => (
            <Field
              key={f.key}
              label={f.label}
              help="Alert fires when a holding falls this % below its prior close."
            >
              <NumberInput
                id={`crash-${f.key}`}
                value={ct[f.key]}
                min={1}
                max={99}
                step={0.5}
                integer={false}
                disabled={!active}
                onCommit={(n) =>
                  save({ crash_thresholds: { ...ct, [f.key]: n } }, 'Crash threshold updated')
                }
              />
            </Field>
          ))}
          <Field
            label="Spike floor (articles)"
            help="Minimum recent articles in the 90-min window before a spike fires."
          >
            <NumberInput
              id="spike-min-abs"
              value={cfg.spike_min_abs ?? 5}
              min={1}
              max={50}
              disabled={!active}
              onCommit={(n) => save({ spike_min_abs: n }, 'Spike floor updated')}
            />
          </Field>
          <Field
            label="Spike multiplier"
            help="Recent count must exceed this multiple of the trailing baseline rate."
          >
            <NumberInput
              id="spike-factor"
              value={cfg.spike_factor ?? 3}
              min={1}
              max={20}
              step={0.1}
              integer={false}
              disabled={!active}
              onCommit={(n) => save({ spike_factor: n }, 'Spike multiplier updated')}
            />
          </Field>
        </div>
      ) : (
        <span className="rd-help">
          Crash-watch: equity {ct.equity}%, commodity {ct.commodity}%, crypto {ct.crypto}%. News
          spike: {cfg.spike_min_abs ?? 5} articles, {cfg.spike_factor ?? 3}× baseline.
        </span>
      )}
    </Panel>
  )
}
