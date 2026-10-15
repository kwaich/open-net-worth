// Settings → AI Model → Model performance. Per-model latency (TTFT/duration/tok-per-sec p50)
// and JSON-retry rate over GET /api/ai/model-telemetry — a read-only snapshot of
// ModelTelemetryStore's rolling 50-event window (RFC 2026-09-04-ai-model-performance-telemetry,
// backend/services/model_telemetry.py).
//
// Placement note: the PRD's original target (ModelSettings.tsx + CostPanel) is SUPERSEDED and
// unreachable from the app (see that file's own header). This panel lives instead in the LIVE
// Settings → AI Model surface, ModelSection.tsx, stacked after the "AI spend" panel — and uses
// THIS directory's `ds.tsx` primitives, not `components/ui/`, because settings.css's flattening
// rules key off ds.tsx's exact DOM shape.
//
// D1 (RFC §0): an error joins the rolling window the latency percentiles are drawn from, so
// `error_rate` is a windowed "lately" signal, not a lifetime one — and it mixes two populations
// (assistant turns and background structured-output attempts). The panel leads with the count
// and its denominator, never a bare percentage, and the caveat line at the top says so.
import { useState } from 'react'
import { authFetch } from '../../../../api/client'
import { useCachedFetchState } from '../../../../state/queryCache'
import { Badge, Btn, Empty, Panel, useSectionBusy } from '../ds'
import type { BadgeTone } from '../ds'

export interface ModelTelemetryRow {
  model: string
  /** Derived display label (never a recorded fact) — null when the catalog doesn't know the id. */
  provider: string | null
  ttft_ms_p50: number | null
  duration_ms_p50: number | null
  tok_per_sec_p50: number | null
  /** Windowed, per D1 — errors_in_window / (latency samples + errors) in the window, NOT a
   *  lifetime ratio. */
  error_rate: number
  /** Absolute count of error entries in the window. */
  errors: number
  /** Count of LATENCY samples in the window (excludes error entries) — never exceeds 50. */
  samples: number
}

const TELEMETRY_PATH = '/api/ai/model-telemetry'

async function getJson<T>(path: string): Promise<T> {
  const r = await authFetch(path)
  if (!r.ok) throw new Error(String(r.status))
  return r.json() as Promise<T>
}

function fmtMs(ms: number | null): string {
  if (ms == null) return '–'
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
}

function fmtTokPerSec(v: number | null): string {
  return v == null ? '–' : `${v.toFixed(1)} tok/s`
}

/** A nonzero rate that would round to "0%" must never read as "no retries" — D1. */
function fmtRatePct(rate: number): string {
  if (rate <= 0) return '0%'
  const pct = rate * 100
  return pct < 1 ? '<1%' : `${Math.round(pct)}%`
}

function badgeTone(rate: number): BadgeTone {
  if (rate <= 0) return 'good'
  if (rate < 0.1) return 'warn'
  return 'bad'
}

/** "{provider} — {model id}" when the catalog knows the provider; the bare id otherwise —
 *  never the literal string "Unknown". */
function modelLabel(row: ModelTelemetryRow): string {
  return row.provider ? `${row.provider} — ${row.model}` : row.model
}

const DURATION_TOOLTIP = 'Older samples predate duration tracking — fills in as new turns run'

function TelemetryRow({ row }: { row: ModelTelemetryRow }) {
  const denom = row.samples + row.errors
  return (
    <tr>
      <th className="rd-telemetry-model" scope="row">
        <span className="rd-telemetry-row-label">{modelLabel(row)}</span>
        <span className="rd-telemetry-row-samples">
          {row.samples} sample{row.samples === 1 ? '' : 's'}
        </span>
      </th>
      <td className="rd-telemetry-metric" data-label="TTFT p50">
        {fmtMs(row.ttft_ms_p50)}
      </td>
      <td className="rd-telemetry-metric" data-label="Duration p50">
        <span title={row.duration_ms_p50 == null ? DURATION_TOOLTIP : undefined}>
          {fmtMs(row.duration_ms_p50)}
        </span>
      </td>
      <td className="rd-telemetry-metric" data-label="Tok/sec p50">
        {fmtTokPerSec(row.tok_per_sec_p50)}
      </td>
      <td className="rd-telemetry-reliability" data-label="JSON-retry rate">
        <Badge tone={badgeTone(row.error_rate)}>
          {row.errors} of {denom} · {fmtRatePct(row.error_rate)} JSON-retry rate
        </Badge>
      </td>
    </tr>
  )
}

export function ModelTelemetryPanel() {
  const [retrying, setRetrying] = useState(false)
  const query = useCachedFetchState<{ models: ModelTelemetryRow[] }>(
    TELEMETRY_PATH,
    () => getJson<{ models: ModelTelemetryRow[] }>(TELEMETRY_PATH),
    { staleTime: 30_000 },
  )
  // Section-level first-load skeleton, not a local spinner — matches every other fetch in
  // ModelSection.tsx. A later background refetch failure (stale data still on screen) does not
  // re-trigger this; only a true first load does.
  useSectionBusy(query.initialLoading && !query.data)

  const rows = query.data?.models ?? []
  const showError = Boolean(query.error && !query.data)
  const showEmpty = !showError && !query.initialLoading && rows.length === 0

  async function retry() {
    setRetrying(true)
    try {
      await query.refresh()
    } finally {
      setRetrying(false)
    }
  }

  return (
    <Panel
      title="Model performance"
      sub="Latency and structured-output reliability, per model, over the last 50 recorded events"
    >
      <span className="rd-help">
        Grouped by model id — if the same model runs through two provider configs, their numbers are
        pooled here. JSON-retry rate only counts structured-output retries from background
        completions, not every kind of failure or every chat turn.
      </span>

      {showError ? (
        <Empty
          title="Could not load model performance"
          body="Something went wrong reaching the server."
          action={
            <Btn size="xs" onClick={() => void retry()}>
              {retrying ? 'Retrying…' : 'Retry'}
            </Btn>
          }
        />
      ) : showEmpty ? (
        <Empty
          title="No telemetry yet"
          body="Performance data appears here after Lucius completes its first AI turn."
        />
      ) : (
        <div className="rd-telemetry-table-wrap">
          <table className="rd-telemetry-table" aria-label="Model performance comparison">
            <thead>
              <tr>
                <th scope="col">Model</th>
                <th scope="col">TTFT p50</th>
                <th scope="col">Duration p50</th>
                <th scope="col">Tok/sec p50</th>
                <th scope="col">JSON-retry rate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <TelemetryRow key={row.model} row={row} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
