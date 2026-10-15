// FreshnessChip — shows a coloured dot + relative-time label for data freshness.
//
// Grade priority: when `grade` is provided (from the backend asset-detail response),
// it takes precedence over the timestamp-based computation. Use the timestamp-only
// path for feed-header price/news chips that have no backend grade.
//
// Grade→tone mapping (backend grades: fresh ≤3d / aging ≤14d / stale):
//   fresh  → --freshness-chip--fresh  (green,  var(--good))
//   aging  → --freshness-chip--stale  (amber,  var(--warn))  ← "aging" maps to stale-tone
//   stale  → --freshness-chip--old    (red,    var(--bad))

import { fDateTime } from '../../utils/format'

type BackendGrade = 'fresh' | 'aging' | 'stale'
type ChipTone = 'fresh' | 'stale' | 'old'

function gradeFromBackend(grade: BackendGrade): ChipTone {
  if (grade === 'fresh') return 'fresh'
  if (grade === 'aging') return 'stale'
  return 'old'
}

function gradeFromTimestamp(
  timestamp: string | undefined,
  intervalSeconds: number | undefined,
): ChipTone {
  if (!timestamp) return 'old'
  try {
    const d = new Date(timestamp)
    if (isNaN(d.getTime())) return 'old'
    const ageMs = Date.now() - d.getTime()
    const ageS = ageMs / 1000
    // Feed-header overdue check: if intervalSeconds is provided and age exceeds 1.5×
    // the configured cadence, the loop likely isn't firing — show old.
    if (intervalSeconds != null && ageS > intervalSeconds * 1.5) return 'old'
    const ageH = ageS / 3600
    if (ageH < 24) return 'fresh'
    const ageD = ageH / 24
    if (ageD < 7) return 'stale'
    return 'old'
  } catch {
    return 'old'
  }
}

function relativeLabel(timestamp: string | undefined): string {
  if (!timestamp) return 'never'
  try {
    const d = new Date(timestamp)
    if (isNaN(d.getTime())) return 'never'
    const s = Math.floor((Date.now() - d.getTime()) / 1000)
    if (s < 60) return 'just now'
    const m = Math.floor(s / 60)
    if (m < 60) return `${m}m ago`
    const h = Math.floor(m / 60)
    if (h < 24) return `${h}h ago`
    const days = Math.floor(h / 24)
    if (days === 1) return 'yesterday'
    if (days < 7) return `${days}d ago`
    if (days < 31) return `${Math.floor(days / 7)}w ago`
    return `${Math.floor(days / 30)}mo ago`
  } catch {
    return 'never'
  }
}

interface Props {
  /** Human-readable prefix shown before the relative time, e.g. "Prices" or "News". */
  label?: string
  /** ISO timestamp of the last update. */
  timestamp?: string
  /** Backend-computed grade (preferred over timestamp computation when provided). */
  grade?: BackendGrade
  /** Configured scan interval in seconds — used for the feed-header overdue check. */
  intervalSeconds?: number
}

export function FreshnessChip({ label, timestamp, grade, intervalSeconds }: Props) {
  const tone: ChipTone = grade
    ? gradeFromBackend(grade)
    : gradeFromTimestamp(timestamp, intervalSeconds)

  // "News never" read like a broken feature; before the first run say what is actually true.
  const relTime = timestamp ? relativeLabel(timestamp) : 'not checked yet'
  const displayText = label ? `${label} ${relTime}` : relTime
  const what = label ? label.toLowerCase() : 'this'
  const titleAttr = timestamp
    ? `Last ${what} check: ${fDateTime(timestamp)}`
    : `No ${what} check has run yet — it runs on its schedule, or press Scan.`

  return (
    <span
      className={`freshness-chip freshness-chip--${tone}`}
      title={titleAttr}
      aria-label={`Freshness: ${displayText}`}
    >
      <span className="freshness-chip__dot" aria-hidden="true" />
      <span className="freshness-chip__label">{displayText}</span>
    </span>
  )
}
