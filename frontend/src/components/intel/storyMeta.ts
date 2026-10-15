// Shared presentation metadata + helpers for Intel story arcs — consumed by the
// Lucius Brief digest columns and the Story feed cards so both read identically.
// Colors resolve to the app's palette tokens (see index.css :root).

import { fBase } from '../../utils/format'
import type { BriefOpinion } from '../../types/intelDigest'

export interface SignalSource {
  title: string
  url: string
  published_at?: string
  domain?: string
  quote?: string
}

export interface TimelineEvent {
  id: string
  date: string
  title: string
  summary: string
  sources: SignalSource[]
  // Corroboration state (ADR 2026-08-28-sourced-and-corroborated-intel-signal-updates):
  // "flagged" = an uncorroborated reversal, held pending a second independent source.
  // superseded = a later, corroborated reversal invalidated this entry. Absent on the
  // common (confirmed, current) case.
  status?: string
  superseded?: boolean
}

export interface AffectedHolding {
  instrument_id: string
  label: string
  ticker: string | null
  /** Null when the holding's currency has no FX rate to the base currency. */
  base_value: number | null
  impact: 'fx_exact' | 'directional'
  direction: 'up' | 'down' | 'unknown'
}

export type StoryType = 'risk' | 'opportunity' | 'price_alert' | 'macro' | 'topic' | 'asset'

export type StoryStatus = 'action_needed' | 'active' | 'monitoring'

export interface StoryArc {
  id: string
  type: StoryType
  label: string
  ticker?: string
  status: StoryStatus
  summary: string
  portfolio_impact?: string
  action_items: string[]
  // F11 fix (owner-reported, 2026-08-29): GET /api/intel/signals bounds this to the most
  // recent `showing_timeline` of `total_timeline` stored entries (storage itself is now
  // unbounded — see backend/services/intel_signals.py). Both fields absent means the story
  // came from GET /api/intel/signals/{id} instead, which returns the full, unbounded array —
  // treat that as "showing == total == timeline.length".
  timeline: TimelineEvent[]
  showing_timeline?: number
  total_timeline?: number
  created_at: string
  updated_at: string
  affected_holdings?: AffectedHolding[]
  // Lifecycle (server-persisted): read / dismissed (user soft-archive) /
  // resolved (auto, trigger cleared) / archived (stale sweep).
  read?: boolean
  dismissed?: boolean
  resolved?: boolean
  resolved_reason?: string
  archived?: boolean
  // A deterministic price-signal story on a watched ticker (services/watch_signals.py) — it
  // restates the ticker's pulse flags, so a ticker row shows it as "Price signal", not its headline.
  watch?: boolean
  watch_kinds?: string[]
  // The explained watch alert (monitoring/watch_analyst.py): only for a material trigger, only
  // with a sourced "why". Never advice — "worth weighing" is considerations, not instructions.
  explained?: {
    trigger: string
    what_happened: string
    why: string
    what_it_means: string
    worth_weighing: string[]
    sources: SignalSource[]
    at: string
  } & BriefOpinion
  // Lucius's in-app read on a watch story (monitoring/watch_reader.py): what it means for someone
  // watching and what to watch next — on every live watch story the explained alert doesn't cover.
  lucius_read?: {
    text: string
    watch_for: string[]
    sources: SignalSource[]
    at: string
  } & BriefOpinion
}

// type → label + accent color. Mirrors the handoff's SIGNAL_META, with 'asset'
// (a backend type the handoff folds into generic updates) mapped to the accent.
export const SIGNAL_META: Record<StoryType, { label: string; color: string }> = {
  risk: { label: 'Risk', color: 'var(--bad)' },
  opportunity: { label: 'Opportunity', color: 'var(--good)' },
  price_alert: { label: 'Price alert', color: 'var(--accent)' },
  macro: { label: 'Macro', color: 'var(--warn)' },
  topic: { label: 'Topic', color: 'var(--crypto)' },
  asset: { label: 'Update', color: 'var(--accent)' },
}

// status → label + dot color + sort rank (lower floats to the top of the feed).
export const STATUS_META: Record<StoryStatus, { label: string; color: string; rank: number }> = {
  action_needed: { label: 'Needs attention', color: 'var(--bad)', rank: 0 },
  active: { label: 'Active', color: 'var(--good)', rank: 1 },
  monitoring: { label: 'Monitoring', color: 'var(--faint)', rank: 2 },
}

// Safe lookups — real backend stories may carry a type/status this UI hasn't
// enumerated; never let an unknown value crash a render (fall back to a neutral).
export function signalMeta(type: string | undefined): { label: string; color: string } {
  return SIGNAL_META[(type ?? '') as StoryType] ?? { label: 'Update', color: 'var(--accent)' }
}
export function statusMeta(status: string | undefined): {
  label: string
  color: string
  rank: number
} {
  return STATUS_META[(status ?? '') as StoryStatus] ?? STATUS_META.monitoring
}

export type SourceKind = 'news' | 'filing' | 'data' | 'analyst'

// Provenance kinds — color-coded so a primary filing reads differently to a news take.
export const SOURCE_KIND: Record<SourceKind, { color: string; label: string }> = {
  news: { color: 'var(--accent)', label: 'News' },
  filing: { color: 'var(--good)', label: 'Filing / primary' },
  data: { color: 'var(--warn)', label: 'Data feed' },
  analyst: { color: 'var(--crypto)', label: 'Analyst' },
}

export interface CitationPill {
  name: string
  kind: SourceKind
  detail: string
  url?: string
}

// The freshest development's title reads as the card headline; the story's own
// summary is Lucius's read below it. Falls back to the display label.
export function storyHeadline(s: StoryArc): string {
  return s.timeline?.[0]?.title?.trim() || s.label
}

function prettyDomain(d: string): string {
  return d.replace(/^www\./, '')
}

// Backend sources carry no explicit kind; infer an obvious few from the domain
// (government/IR → primary filing), default to news. (FLAG: backend does not yet
// classify source kind — see gaps note.)
function inferKind(src: SignalSource): SourceKind {
  const d = (src.domain || src.url || '').toLowerCase()
  if (/sec\.gov|\.gov|investor|\bir\b|annualreport/.test(d)) return 'filing'
  if (/feed|coinbase|yahoo|tradingview|farside/.test(d)) return 'data'
  return 'news'
}

// The always-visible citations must support the current headline, not an older
// version of the story. The full timeline still shows historical sources.
export function aggregateSources(s: StoryArc, limit = 6): CitationPill[] {
  const seen = new Set<string>()
  const out: CitationPill[] = []
  for (const ev of s.timeline?.slice(0, 1) || []) {
    for (const src of ev.sources || []) {
      const name = prettyDomain(src.domain || src.title || '')
      if (!name) continue
      const key = (src.url || src.title || name).toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      const detail = [src.title, src.published_at].filter(Boolean).join(' • ')
      out.push({ name, kind: inferKind(src), detail: detail || name, url: src.url })
      if (out.length >= limit) return out
    }
  }
  return out
}

// Explicit-signed base-currency figure: +S$2,410 / −S$1,840.
export function fSignedBase(n: number): string {
  return (n >= 0 ? '+' : '−') + fBase(Math.abs(n))
}
