import { QUEUE_META, classifySignal } from './signalQueue'
import type { StoryArc } from './storyMeta'
import type { WatchPulseRow } from '../../types/watchPulse'

// Grouping model behind TickerDesk: one group per ticker (its pulse row + its stories), plus the
// tickerless stories (portfolio-wide ones — a list tab claims only stories on its tickers).

// Urgency is the signal QUEUE's (signalQueue.classifySignal): a stale "action needed" ranks as
// "needs recheck", and one whose only action is "No action needed" as monitoring.
const rankOf = (s: StoryArc) => QUEUE_META[classifySignal(s)].rank
export const QUIET_RANK = 4

export function byUrgency(a: StoryArc, b: StoryArc): number {
  const r = rankOf(a) - rankOf(b)
  return r !== 0 ? r : (b.updated_at || '').localeCompare(a.updated_at || '')
}

export interface NameGroup {
  key: string
  row: WatchPulseRow | null
  stories: StoryArc[]
  rank: number
}

const tickerKey = (t: string | null | undefined) => (t ?? '').trim().toUpperCase()

export function groupByName(
  rows: WatchPulseRow[],
  feed: StoryArc[],
  { order }: { order: 'urgency' | 'given' },
) {
  const byTicker = new Map<string, StoryArc[]>()
  const portfolio: StoryArc[] = []
  for (const s of feed) {
    const t = tickerKey(s.ticker)
    if (!t) portfolio.push(s)
    else byTicker.set(t, [...(byTicker.get(t) ?? []), s])
  }
  const groups: NameGroup[] = rows.map((row) => ({
    key: tickerKey(row.ticker),
    row,
    stories: (byTicker.get(tickerKey(row.ticker)) ?? []).sort(byUrgency),
    rank: QUIET_RANK,
  }))
  // A name with stories but no quote (a bond, an option, an unpriced coin) still gets a row.
  const priced = new Set(groups.map((g) => g.key))
  for (const [key, stories] of byTicker) {
    if (priced.has(key)) continue
    groups.push({ key, row: null, stories: stories.sort(byUrgency), rank: 0 })
  }
  for (const g of groups) {
    g.rank = g.stories.length ? rankOf(g.stories[0]!) : QUIET_RANK
  }
  if (order === 'urgency') {
    groups.sort(
      (a, b) =>
        a.rank - b.rank ||
        (b.row?.score ?? 0) - (a.row?.score ?? 0) ||
        (b.row?.position?.weight ?? 0) - (a.row?.position?.weight ?? 0),
    )
  }
  return { groups, portfolio: portfolio.sort(byUrgency) }
}
