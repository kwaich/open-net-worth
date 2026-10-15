// The Holdings brief's template text (no LLM), at the user's brief verbosity (Settings ›
// Intelligence): 1 today's book move only, 2 adds the day's best and worst contributor, 3 (the
// default) adds the gap to the benchmark over the period, 4 adds the day's top movers and the
// period's biggest driver. Level 5 is Lucius's narrative (the digest's `narration`), which falls
// back to level 4 while it loads or if it fails. What needs a decision is left to the desk's
// status bar below, which says it already.
import { fPct } from '../../utils/format'
import { fSignedBase } from './storyMeta'
import type { IntelDigest, TopMover } from '../../types/intelDigest'
import type { BriefLevel } from './watchBrief'

const EMPTY_TODAY = { pl_base: 0, pl_pct: 0, has_data: false }

function periodLabel(p: string): string {
  return p === 'ALL' ? 'all-time' : p
}

// Top winner + worst loser by base-currency day change (the figure, not the %).
function bestWorst(movers: TopMover[]): { best?: TopMover; worst?: TopMover } {
  const withBase = movers.filter((m) => m.day_change_base != null)
  if (withBase.length === 0) return {}
  const sorted = [...withBase].sort((a, b) => (b.day_change_base ?? 0) - (a.day_change_base ?? 0))
  return { best: sorted[0], worst: sorted[sorted.length - 1] }
}

const name = (m: { ticker: string | null; label: string }) => m.ticker || m.label

function headline(digest: IntelDigest): string {
  const today = digest.today ?? EMPTY_TODAY
  return today.has_data
    ? `Your book is ${fSignedBase(today.pl_base)} (${fPct(today.pl_pct, { fraction: true, sign: true })}) today`
    : 'No portfolio movement data yet'
}

function contributors(digest: IntelDigest): string {
  const { best, worst } = bestWorst(digest.top_movers ?? [])
  const segs: string[] = []
  if (best && (best.day_change_base ?? 0) > 0)
    segs.push(`${name(best)} led (${fSignedBase(best.day_change_base ?? 0)})`)
  if (worst && worst !== best && (worst.day_change_base ?? 0) < 0)
    segs.push(`${name(worst)} dragged (${fSignedBase(worst.day_change_base ?? 0)})`)
  return segs.length ? ` — ${segs.join(', ')}` : ''
}

function benchmarkGap(digest: IntelDigest): string | null {
  const port = digest.portfolio_return?.period_return ?? null
  const benchR = digest.market_backdrop?.benchmark?.twr?.period_return ?? null
  const benchSym = digest.market_backdrop?.benchmark?.symbol
  if (port == null || benchR == null || !benchSym) return null
  const pts = (port - benchR) * 100
  return `You're ${Math.abs(pts).toFixed(1)} pts ${pts >= 0 ? 'ahead of' : 'behind'} ${benchSym} ${periodLabel(digest.period)}.`
}

function detail(digest: IntelDigest): string | null {
  const movers = [...(digest.top_movers ?? [])]
    .sort((a, b) => Math.abs(b.day_change_pct) - Math.abs(a.day_change_pct))
    .slice(0, 3)
    .map((m) => `${name(m)} ${fPct(m.day_change_pct, { sign: true })}`)
  const driver = (digest.return_drivers ?? []).find((d) => d.contribution_base != null)
  const segs: string[] = []
  if (movers.length) segs.push(`Top movers: ${movers.join(', ')}.`)
  if (driver)
    segs.push(
      `The ${periodLabel(digest.period)} biggest driver: ${name(driver)} (${fSignedBase(driver.contribution_base ?? 0)}).`,
    )
  return segs.length ? segs.join(' ') : null
}

export function holdingsBrief(digest: IntelDigest, level: BriefLevel = 3): string {
  if (level === 1) return `${headline(digest)}.`
  const parts = [`${headline(digest)}${contributors(digest)}.`]
  if (level >= 3) {
    const gap = benchmarkGap(digest)
    if (gap) parts.push(gap)
  }
  if (level >= 4) {
    const more = detail(digest)
    if (more) parts.push(more)
  }
  return parts.join(' ')
}
