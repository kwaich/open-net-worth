import { EntityRow } from '../../lucius/EntityRow'
import { X } from '../../ui/icons'
import { Badge, Select, Switch, Text } from '../ui'
import type { BadgeTone } from '../ui'
import { fDuration } from '../../utils/format'

// INT9 Phase C — the News sources panel's row shape, mirroring GET /api/intel/sources'
// per-row wire shape exactly (backend/api/news_sources.py + services/news_sources.list_sources
// + monitoring.rss_poller_hot.source_health_snapshot's join).
export interface NewsSourceLatency {
  n: number
  median_hours: number
  p75_hours: number
}

export interface NewsSourceFailureStreak {
  consecutive_failures: number
  backing_off: boolean
  skip_remaining_ticks: number
}

export interface NewsSource {
  id: string
  label: string
  kind: 'regulatory' | 'outlet' | 'ticker' | 'search' | 'topic_engine' | 'custom'
  transport: string
  url: string | null
  resolver: string | null
  scope: 'firehose' | 'per_entity'
  match: string
  cadence: 'hot' | 'fast' | 'slow'
  via: string
  enabled: boolean
  min_interval_sec: number
  markets: string[]
  provenance: 'builtin' | 'user'
  notes: string
  added_at: string
  latency: NewsSourceLatency | null
  last_ok_at: string | null
  failure_streak: NewsSourceFailureStreak | null
}

export const KIND_LABEL: Record<NewsSource['kind'], string> = {
  regulatory: 'Regulatory',
  outlet: 'Outlet',
  ticker: 'Ticker',
  search: 'Search',
  topic_engine: 'Topic engine',
  custom: 'Custom feed',
}

export const CADENCE_LABEL: Record<NewsSource['cadence'], string> = {
  hot: 'Hot',
  fast: 'Fast',
  slow: 'Slow',
}

export const CADENCE_OPTIONS = (Object.keys(CADENCE_LABEL) as NewsSource['cadence'][]).map(
  (value) => ({ value, label: CADENCE_LABEL[value] }),
)

// Mirrors monitoring/rss_poller_health.py's _FAILURE_STREAK_THRESHOLD — a source is "Failing"
// once its recorded consecutive-failure count reaches this, or it's already in a backoff window
// (backing_off implies the threshold was already crossed, kept as a belt-and-suspenders check).
const FAILURE_STREAK_THRESHOLD = 3

// Kinds that never escalate a "no data in the last 7 days" reading to the red "No data in 7d"
// state, even when their row's scope is "firehose" (topic_engine — a single shared GDELT query
// across every watched topic — genuinely goes quiet for stretches with no matching hits; ticker/
// search are per-entity by nature). Only a firehose kind OUTSIDE this set (regulatory/outlet/
// custom) escalates once it's been added for over 24h with zero evidence ever seen.
const NEVER_ESCALATE_NO_DATA_KINDS = new Set<NewsSource['kind']>([
  'ticker',
  'search',
  'topic_engine',
])

export interface SourceHealth {
  tone: BadgeTone
  label: string
  detail: string
}

/** Hours→string: <1h "{m}m", <48h "{h.toFixed(1)}h", else "{d.toFixed(1)}d". */
export function formatLag(hours: number): string {
  if (hours < 1) return `${Math.round(hours * 60)}m`
  if (hours < 48) return `${hours.toFixed(1)}h`
  return `${(hours / 24).toFixed(1)}d`
}

/**
 * classifySourceHealth — the Health chip's bucketing (spec: disabled overrides everything;
 * failing overrides fresh/stale latency; a null latency splits on scope+kind+age; otherwise the
 * median-hours thresholds decide Fast/Slow/Stalled). Pure — no I/O, injects no clock parameter
 * (uses Date.now(), matching this codebase's other timestamp-classifier convention, e.g.
 * FreshnessChip.tsx).
 */
export function classifySourceHealth(row: NewsSource): SourceHealth {
  if (!row.enabled) {
    return { tone: 'muted', label: 'Disabled', detail: 'You turned this off.' }
  }

  const fs = row.failure_streak
  if (fs && (fs.backing_off || fs.consecutive_failures >= FAILURE_STREAK_THRESHOLD)) {
    return {
      tone: 'neg',
      label: 'Failing',
      detail: 'Recent fetches are failing — Lucius is backing off.',
    }
  }

  if (!row.latency) {
    const canEscalate = row.scope === 'firehose' && !NEVER_ESCALATE_NO_DATA_KINDS.has(row.kind)
    const addedHoursAgo = row.added_at
      ? (Date.now() - new Date(row.added_at).getTime()) / 3600000
      : 0
    if (canEscalate && addedHoursAgo > 24) {
      return {
        tone: 'neg',
        label: 'No data in 7d',
        detail: 'Nothing from this feed in the last week — it may be down.',
      }
    }
    return {
      tone: 'muted',
      label: 'No data yet',
      detail: 'No stories logged from this source in the last 7 days.',
    }
  }

  const { median_hours, p75_hours } = row.latency
  const detail = `Median ${formatLag(median_hours)}, p75 ${formatLag(p75_hours)}`
  if (median_hours <= 1) return { tone: 'pos', label: 'Fast', detail }
  if (median_hours <= 6) return { tone: 'warn', label: 'Slow', detail }
  return { tone: 'neg', label: 'Stalled', detail }
}

interface Props {
  row: NewsSource
  onToggle: (row: NewsSource, next: boolean) => void
  onCadenceChange: (row: NewsSource, next: NewsSource['cadence']) => void
  onDeleteRequest: (row: NewsSource) => void
}

export function NewsSourceRow({ row, onToggle, onCadenceChange, onDeleteRequest }: Props) {
  const health = classifySourceHealth(row)
  const switchTitle = row.enabled
    ? 'Turn off — Lucius stops fetching this source right away.'
    : 'Turn on — Lucius starts fetching this source on its next check.'

  return (
    <EntityRow
      as="tr"
      type="news_source"
      id={row.id}
      label={row.label}
      item={row}
      toFields={(it) => ({
        label: it.label,
        kind: it.kind,
        enabled: it.enabled ? 'on' : 'off',
        cadence: it.cadence,
      })}
      search={`${row.label} ${row.kind}`}
      sublabel={`${row.kind} • ${row.enabled ? 'on' : 'off'}`}
    >
      <td>{row.label}</td>
      <td>{KIND_LABEL[row.kind] ?? row.kind}</td>
      <td>
        <Select
          aria-label={`Cadence for ${row.label}`}
          value={row.cadence}
          options={CADENCE_OPTIONS}
          onChange={(e) => onCadenceChange(row, e.target.value as NewsSource['cadence'])}
        />
        <Text as="div" size="2xs" tone="muted">
          no more than every {fDuration(row.min_interval_sec)}
        </Text>
      </td>
      <td>
        <span title={`${health.label} — ${health.detail}`}>
          <Badge tone={health.tone}>{health.label}</Badge>
        </span>
      </td>
      <td>
        <span title={switchTitle}>
          <Switch
            aria-label={`${row.label} enabled`}
            checked={row.enabled}
            onChange={(e) => onToggle(row, e.target.checked)}
          />
        </span>
      </td>
      <td>
        {row.provenance === 'user' && (
          <button
            type="button"
            className="intel-pa-row-remove"
            title="Remove this feed"
            onClick={() => onDeleteRequest(row)}
          >
            <X size={11} />
          </button>
        )}
      </td>
    </EntityRow>
  )
}
