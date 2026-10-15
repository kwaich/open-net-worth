import type { Automation, AutomationRun } from '../../../api/automations'
import { fDate } from '../../../utils/format'

export function AutomationCostPanel({
  automation,
  runs,
}: {
  automation: Automation
  runs: AutomationRun[]
}) {
  const recordedRuns = runs.filter((run) => run.cost_usd != null)
  const stats = automation.stats
  const perRun = numeric(stats.cost_per_run) ?? average(recordedRuns.map((run) => run.cost_usd!))
  const monthRuns = recordedRuns.filter((run) => inCurrentMonth(run.started_at))
  const month = sum(monthRuns.map((run) => run.cost_usd!))
  const last = recordedRuns[0]?.cost_usd ?? null
  const hasData = perRun != null || month != null || last != null
  const chronological = [...recordedRuns].reverse()
  const max = Math.max(...chronological.map((run) => run.cost_usd ?? 0), 0.001)
  const averageInput = averageNumber(runs.map((run) => run.input_tokens))
  const averageOutput = averageNumber(runs.map((run) => run.output_tokens))
  const trend =
    chronological.length > 1
      ? (chronological.at(-1)?.cost_usd ?? 0) - (chronological[0]?.cost_usd ?? 0)
      : 0
  const expectedRuns = runsPerMonth(automation.trigger)
  return (
    <section className="a3-cost-card" aria-label="Cost and usage">
      {!hasData ? (
        <p className="a3-muted">No cost data yet.</p>
      ) : (
        <>
          <div className="a3-cost-nums">
            <CostMetric
              label="Per run"
              value={money(perRun)}
              detail={recordedRuns.length ? `average of ${recordedRuns.length}` : undefined}
            />
            <CostMetric
              label="This month"
              value={money(month)}
              detail={
                typeof stats.runs_this_month === 'number'
                  ? `${stats.runs_this_month} runs so far`
                  : `${monthRuns.length} runs so far`
              }
            />
            <CostMetric
              label="Last run"
              value={money(last)}
              detail={
                trend === 0 ? 'flat' : `${trend > 0 ? '↑' : '↓'} ${money(Math.abs(trend))} vs first`
              }
            />
          </div>
          {chronological.length > 1 && (
            <div className="a3-cost-bars-wrap">
              <div className="a3-bars" data-dense={chronological.length > 24 || undefined}>
                {chronological.map((run) => (
                  <span
                    key={run.id}
                    className={`a3-bar${run.status === 'failed' ? ' bad' : ''}`}
                    style={{ height: Math.max(8, Math.round(((run.cost_usd ?? 0) / max) * 46)) }}
                    title={`${fDate(run.started_at)} • ${money(run.cost_usd)}`}
                  />
                ))}
              </div>
              <div className="a3-bars-x">
                <span>{fDate(chronological[0]!.started_at)}</span>
                <span>{fDate(chronological.at(-1)!.started_at)}</span>
              </div>
              <div className="a3-bars-note">
                Each bar is one run
                {automation.status === 'active' && expectedRuns != null && perRun != null
                  ? ` • about ${expectedRuns} expected this month, ~${money(perRun * expectedRuns)}`
                  : ''}
              </div>
            </div>
          )}
          <div className="a3-tokens">
            Avg tokens <b>{tokenCount(averageInput)} in</b> • <b>{tokenCount(averageOutput)} out</b>
          </div>
        </>
      )}
    </section>
  )
}

export function money(value: number | null | undefined) {
  if (value == null) return '—'
  const digits = value > 0 && value < 0.1 ? 3 : 2
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value)
}

export function tokenCount(value: number | null | undefined) {
  if (value == null) return '—'
  return new Intl.NumberFormat('en-US', {
    notation: value >= 1000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(value)
}

function CostMetric({ label, value, detail }: { label: string; value: string; detail?: string }) {
  return (
    <div className="a3-cost-num">
      <span className="k">{label}</span>
      <span className="v">{value}</span>
      {detail && <span className="s">{detail}</span>}
    </div>
  )
}

function numeric(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function average(values: number[]): number | null {
  return values.length ? sum(values)! / values.length : null
}

function sum(values: number[]): number | null {
  return values.length ? values.reduce((total, value) => total + value, 0) : null
}

function averageNumber(values: Array<number | null>): number | null {
  return average(values.filter((value): value is number => value != null))
}

function inCurrentMonth(value: string) {
  const date = new Date(value)
  const now = new Date()
  return date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth()
}

function runsPerMonth(trigger: Automation['trigger']): number | null {
  if (trigger.kind !== 'schedule') return trigger.kind === 'manual' ? 0 : null
  if (trigger.type === 'daily') return 30
  if (trigger.type === 'weekdays') return 22
  if (trigger.type === 'weekly') return 4
  if (trigger.type === 'monthly' || trigger.type === 'once') return 1
  if (trigger.type === 'interval' && trigger.seconds)
    return Math.round((30 * 24 * 3600) / trigger.seconds)
  return null
}
