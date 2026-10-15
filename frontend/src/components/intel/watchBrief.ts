// The Watchlist pulse's brief, written from the same rows the list shows (no LLM — like the
// Holdings brief's own text): the day's most unusual move, what's scheduled this week, and who is
// furthest ahead of / behind its comparison over the period on screen. How much of that it says
// follows the user's brief verbosity (Settings › Intelligence): 1 the loudest move only, 2 adds
// the next event, 3 (default) adds the period's best/worst in about two lines, 4 adds every event
// this week and the up/down/flagged counts. Level 5 is Lucius's narrative (useWatchBrief), which
// falls back to level 4 while it loads.

import { compact1, pct1 } from './pulseHelp'
import type { NextEvent, TrendPeriod, WatchPulseRow } from '../../types/watchPulse'

const MAX_LEN = 220 // ≈ two lines of the brief at desktop width
const DETAILED_MAX_LEN = 420 // ≈ three to four lines

export type BriefLevel = 1 | 2 | 3 | 4 | 5

const PERIOD_PHRASE: Record<TrendPeriod, string> = {
  '1M': 'Over the past month',
  '3M': 'Over the past 3 months',
  '1Y': 'Over the past year',
}

const DRIVER: Record<string, string> = {
  company: 'company-specific',
  sector: 'with its sector',
  market: 'with the market',
}

function today(rows: WatchPulseRow[], listName?: string | null, headline = false): string | null {
  const priced = rows.filter((r) => r.day_pct != null)
  if (!priced.length) return null
  const loud = priced
    .filter((r) => r.move?.z != null && Math.abs(r.move.z) >= 2)
    .sort((a, b) => Math.abs(b.move!.z!) - Math.abs(a.move!.z!))[0]
  if (loud) {
    const d = loud.day_pct!
    const why = loud.move?.kind && DRIVER[loud.move.kind] ? `, ${DRIVER[loud.move.kind]}` : ''
    return (
      `${loud.ticker} ${d >= 0 ? 'rose' : 'fell'} ${Math.abs(d).toFixed(1)}% today — ` +
      `${compact1(Math.abs(loud.move!.z!))}× its usual move${why}.`
    )
  }
  const n = priced.length
  const names = listName
    ? `the ${n} name${n === 1 ? '' : 's'} on ${listName}`
    : `your ${n} watched name${n === 1 ? '' : 's'}`
  const top = [...priced].sort((a, b) => Math.abs(b.day_pct!) - Math.abs(a.day_pct!))[0]!
  if (Math.abs(top.day_pct!) < 0.05) return `A quiet day across ${names}.`
  if (headline) return `${top.ticker} moved most today, ${pct1(top.day_pct!)}.`
  return `A normal day across ${names}; ${top.ticker} moved most, ${pct1(top.day_pct!)}.`
}

function when(days: number): string {
  return days === 0 ? 'today' : days === 1 ? 'tomorrow' : `in ${days} days`
}

function coming(rows: WatchPulseRow[], limit = 2): string | null {
  const soon = rows
    .filter((r): r is WatchPulseRow & { next_event: NextEvent } => !!r.next_event)
    .filter((r) => r.next_event.in_days <= 7)
    .sort((a, b) => a.next_event.in_days - b.next_event.in_days)
  const [first, ...rest] = soon
  if (!first) return null
  const e = first.next_event
  let line = `${first.ticker} ${e.kind === 'earnings' ? 'reports earnings' : 'goes ex-dividend'} ${when(e.in_days)}`
  if (rest.length && limit > 0) {
    const named = rest
      .slice(0, limit)
      .map((r) => `${r.ticker} (${r.next_event.kind === 'earnings' ? 'earnings' : 'ex-dividend'})`)
    const more = rest.length - named.length
    line += `; also this week: ${named.join(', ')}${more > 0 ? ` and ${more} more` : ''}`
  }
  return `${line}.`
}

function trend(rows: WatchPulseRow[], period: TrendPeriod, benchSymbol?: string | null) {
  const gaps = rows
    .map((r) => ({ r, gap: r.trend?.[period]?.vs_bench_pct }))
    .filter((x): x is { r: WatchPulseRow; gap: number } => x.gap != null)
    .sort((a, b) => b.gap - a.gap)
  if (!gaps.length) return null
  const vs = (x: { r: WatchPulseRow; gap: number }) =>
    `${pct1(x.gap)} vs ${x.r.compare?.symbol || benchSymbol || 'its comparison'}`
  const best = gaps[0]!
  const worst = gaps[gaps.length - 1]!
  if (gaps.length === 1) return `${PERIOD_PHRASE[period]}, ${best.r.ticker} is ${vs(best)}.`
  return (
    `${PERIOD_PHRASE[period]}, ${best.r.ticker} did best against its comparison ` +
    `(${vs(best)}), ${worst.r.ticker} worst (${vs(worst)}).`
  )
}

function counts(rows: WatchPulseRow[]): string | null {
  const priced = rows.filter((r) => r.day_pct != null)
  if (!priced.length) return null
  const up = priced.filter((r) => r.day_pct! >= 0.05).length
  const down = priced.filter((r) => r.day_pct! <= -0.05).length
  const flagged = rows.filter((r) => (r.findings ?? []).length > 0).length
  return `${up} up, ${down} down${flagged ? `, ${flagged} flagged` : ''}.`
}

export function watchBrief(
  rows: WatchPulseRow[],
  period: TrendPeriod,
  benchSymbol?: string | null,
  /** The list the rows are (a list tab), for "the 6 names on Semis". */
  listName?: string | null,
  /** The user's brief verbosity; 5 (Lucius's narrative) renders as 4 here. */
  level: BriefLevel = 3,
): string | null {
  const first = today(rows, listName, level === 1)
  if (!first || level === 1) return first
  const detailed = level >= 4
  const event = coming(rows, level === 2 ? 0 : detailed ? rows.length : 2)
  const lead = event ? `${first} ${event}` : first
  if (level === 2) return lead
  const parts = [lead, trend(rows, period, benchSymbol), detailed ? counts(rows) : null]
  const cap = detailed ? DETAILED_MAX_LEN : MAX_LEN
  let out = lead
  for (const part of parts.slice(1)) {
    if (!part) continue
    if (event && `${out} ${part}`.length > cap) break
    out = `${out} ${part}`
  }
  return out
}
