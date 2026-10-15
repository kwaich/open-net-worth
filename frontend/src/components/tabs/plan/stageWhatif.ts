// stageWhatif.ts — pure fold: plan.stage_whatif command args (snake_case, model-authored,
// UNTRUSTED) → the Outlook's applyState partial + streams. Levers are baseline-relative
// REPLACE: unspecified levers stay undefined (applyState resets them to BASE). Events/streams
// are relative to whatever the CALLER passes as ctx.baseEvents/baseStreams — the caller
// (outlook.tsx) passes the CURRENTLY STAGED list, not the saved baseline, so adds/removes
// accumulate across calls instead of a forgotten re-add silently dropping a prior one. Every
// value is clamped here so the component applies blindly.

export interface WhatifLifeEvent {
  id: string
  label: string
  amount: number
  age: number
}

export interface WhatifIncomeStream {
  label: string
  amount: number
  age: number
  endAge?: number | null
}

export interface WhatifPartial {
  saving?: number
  ret?: number
  vol?: number
  monthlyRetirementSpending?: number
  swr?: number
  lifeExp?: number
  retireAge?: number
  atNumber?: boolean
  events?: WhatifLifeEvent[]
  crash?: boolean
  weakSeq?: boolean
  lostDec?: boolean
  highInfl?: boolean
}

export interface WhatifCtx {
  base: {
    saving: number
    ret: number
    vol: number
    monthlyRetirementSpending: number
    swr: number
    lifeExp: number
    retireAge: number
  }
  baseEvents: WhatifLifeEvent[]
  baseStreams: WhatifIncomeStream[]
  currentAge: number
  lifeExp: number
}

const MAX_ITEMS = 20
const MAX_AMOUNT = 1e9
const MAX_LABEL = 40

function num(v: unknown): number | undefined {
  return typeof v === 'number' && isFinite(v) ? v : undefined
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v))
}

function label(v: unknown, fallback: string): string {
  const s = typeof v === 'string' ? v.trim().slice(0, MAX_LABEL) : ''
  return s || fallback
}

// Models vary in what they call the display field — accept the common synonyms so a
// what-if event never degrades to a bare "Event" chip (live repro: grok sent `note`).
function itemLabel(r: Record<string, unknown>, fallback: string): string {
  return label(r.label ?? r.note ?? r.name ?? r.description, fallback)
}

function amount(v: unknown): number {
  const n = num(v)
  return n == null ? 0 : clamp(Math.round(n), -MAX_AMOUNT, MAX_AMOUNT)
}

function removeList(v: unknown): string[] {
  const arr = Array.isArray(v) ? v : []
  return arr.map((l) => String(l).trim().toLowerCase()).filter(Boolean)
}

// A caller (the model) recalling a label from earlier in a conversation rarely reproduces it
// byte-for-byte ("porsche" vs the actual "Buy Porsche (Vietnam)") — an EXACT match silently
// removes nothing, which is a worse failure than an over-broad one: it reads as "there's no
// way to remove just this one," and the live fallout was the caller reaching for plan.reset
// and wiping everything instead of the one stale item. Case-insensitive substring, either
// direction; a token under 3 chars is ignored as too unspecific to safely match on.
function matchesAnyRemove(label: string, removes: string[]): boolean {
  const l = label.trim().toLowerCase()
  return removes.some((r) => r.length >= 3 && (l.includes(r) || r.includes(l)))
}

export function buildWhatifState(
  args: Record<string, unknown>,
  ctx: WhatifCtx,
): { partial: WhatifPartial; streams: WhatifIncomeStream[] } {
  const partial: WhatifPartial = {}
  const saving = num(args.saving)
  if (saving != null) partial.saving = Math.max(0, saving)
  const ret = num(args.ret)
  if (ret != null) partial.ret = clamp(ret, 0, 100)
  const vol = num(args.vol)
  if (vol != null) partial.vol = clamp(vol, 0, 100)
  const monthlyRetirementSpending = num(args.monthlyRetirementSpending)
  if (monthlyRetirementSpending != null)
    partial.monthlyRetirementSpending = Math.max(0, monthlyRetirementSpending)
  const swr = num(args.swr)
  if (swr != null) partial.swr = clamp(swr, 0, 100)
  // camelCase is the advertised vocabulary (matches the persisted plan tools); the
  // snake_case fallbacks stay as silent tolerance — staging is ephemeral UI state, so
  // forgiving beats refusing here (the PERSISTED choke refuses, by contrast).
  const lifeExp = num(args.lifeExp ?? args.life_exp)
  if (lifeExp != null) partial.lifeExp = clamp(Math.round(lifeExp), 1, 150)
  const retireAge = num(args.retireAge ?? args.retire_age)
  if (retireAge != null) partial.retireAge = clamp(Math.round(retireAge), 1, 150)
  const atNumber = args.atNumber ?? args.at_number
  if (typeof atNumber === 'boolean') partial.atNumber = atNumber
  if (typeof args.crash === 'boolean') partial.crash = args.crash
  const weakSeq = args.weakSeq ?? args.weak_seq
  if (typeof weakSeq === 'boolean') partial.weakSeq = weakSeq
  const lostDec = args.lostDec ?? args.lost_dec
  if (typeof lostDec === 'boolean') partial.lostDec = lostDec
  const highInfl = args.highInfl ?? args.high_infl
  if (typeof highInfl === 'boolean') partial.highInfl = highInfl

  // ctx.currentAge is the precise, fractional simulation age (e.g. 28.1 from a date_of_birth)
  // — an event/stream age is a discrete year a person picks, never a fraction of one, so every
  // bound and default here is rounded BEFORE it can leak into a clamped result.
  const effLifeExp = Math.round(partial.lifeExp ?? ctx.lifeExp)
  const currentAgeR = Math.round(ctx.currentAge)
  const clampAge = (v: number) =>
    clamp(Math.round(v), currentAgeR, Math.max(currentAgeR, effLifeExp - 1))

  const evRemoves = removeList(args.removeEvents ?? args.remove_events)
  let events = ctx.baseEvents.filter((e) => !matchesAnyRemove(e.label, evRemoves))
  const rawEvAdds = args.addEvents ?? args.add_events
  const evAdds = (Array.isArray(rawEvAdds) ? rawEvAdds : []).slice(0, MAX_ITEMS)
  let n = 0
  for (const raw of evAdds) {
    if (!raw || typeof raw !== 'object') continue
    const r = raw as Record<string, unknown>
    const amt = amount(r.amount)
    if (!amt) continue
    events = [
      ...events,
      {
        // Counter + label (never Date.now()) — deterministic; unique within one staging.
        id: `lx${n++}-${itemLabel(r, 'Event')}`,
        label: itemLabel(r, 'Event'),
        amount: amt,
        age: clampAge(num(r.age) ?? ctx.currentAge + 5),
      },
    ]
  }
  partial.events = events

  const stRemoves = removeList(args.removeStreams ?? args.remove_streams)
  let streams = ctx.baseStreams.filter((s) => !matchesAnyRemove(s.label, stRemoves))
  const rawStAdds = args.addStreams ?? args.add_streams
  for (const raw of (Array.isArray(rawStAdds) ? rawStAdds : []).slice(0, MAX_ITEMS)) {
    if (!raw || typeof raw !== 'object') continue
    const r = raw as Record<string, unknown>
    const amt = amount(r.amount)
    if (!amt) continue
    const age = clampAge(num(r.age) ?? ctx.base.retireAge)
    const end = num(r.endAge ?? r.end_age)
    streams = [
      ...streams,
      {
        label: itemLabel(r, 'Income'),
        amount: amt,
        age,
        endAge: end != null && Math.round(end) > age ? Math.round(end) : null,
      },
    ]
  }
  return { partial, streams }
}
