// Client-side coverage/proposal engine — the design handoff's `Cmt3Cov` module ported over
// REAL backend data instead of a client-only projection. The server (`services/commitments.
// coverage()`) stays the ONE source of truth for the monthly cushion `timeline`; everything
// here is a pure, EXACT re-derivation of that same cumulative-balance series (never a second
// projection engine — see docs/adr/2026-07-22-commitments-tab-redesign-faithful-port.md §2).
//
// Why the re-simulation is EXACT, not approximate: `timeline[i].balance` is a running
// cumulative sum. Moving one dated event (a funding draw, or an installment) by a few months
// only ever adds/removes that event's amount from the months STRICTLY BETWEEN its old and new
// dates — every month before both dates, and every month at-or-after both dates, nets to zero
// change. `windowedDelta` below is that one shared primitive; a top-up is the same shape with
// an open-ended window (the amount lands and then simply stays).
import type {
  Commitment,
  CoverageTimelineMonth,
  PortfolioCoverage,
} from '../../../types/commitments'
import { fBase, getBaseCurrency, toBase } from '../../../utils/format'
import { fmtDate, fmtDay, isTracker, monthLong } from '../../../utils/commitments'

export interface CoverageDerived {
  timeline: CoverageTimelineMonth[]
  floor: number
  minBal: number
  minIdx: number
  /** First index whose balance dips below the floor, or -1 if never. */
  gapIdx: number
  riskId: string | null
  riskDate: string | null
  shortfall: number
}

/** Adapts the server's PortfolioCoverage response into the shape the masthead chart, ribbon,
 *  and proposal generator share — so they can never disagree (all read the same derivation).
 *
 *  `cov.commitments[].earliest_shortfall_date` is now PLAN-FIDELITY (backend
 *  `services/commitments._plan_fidelity_verdict`, 2026-07-31): each commitment's own real
 *  cash-runs-out date, from today's cash + that commitment's own dated events only. The
 *  portfolio-level `cov.earliest_shortfall_date` is a DIFFERENT basis (the household-blended
 *  timeline dipping below the cash floor) and will generally NOT equal any single
 *  commitment's date any more — so the "which commitment is driving this" pick is a pure
 *  client-side read of `cov.commitments[]`: the at-risk commitment with the SOONEST own
 *  shortfall, independent of the household gap month. */
export function deriveClientCoverage(cov: PortfolioCoverage): CoverageDerived {
  const timeline = cov.timeline || []
  const balances = timeline.map((m) => m.balance)
  const minBal = balances.length ? Math.min(...balances) : 0
  const minIdx = balances.indexOf(minBal)
  const floor = cov.low_balance_threshold
  const gapIdx = timeline.findIndex((m) => m.balance < floor)
  const atRisk = cov.commitments
    .filter((c) => c.at_risk && c.earliest_shortfall_date)
    .sort((a, b) => {
      if (a.earliest_shortfall_date !== b.earliest_shortfall_date) {
        return a.earliest_shortfall_date! < b.earliest_shortfall_date! ? -1 : 1
      }
      return b.shortfall_base - a.shortfall_base // same-date tie → the bigger gap leads
    })
  const riskId = atRisk[0]?.id ?? null
  const riskDate = atRisk[0]?.earliest_shortfall_date ?? null
  return {
    timeline,
    floor,
    minBal,
    minIdx,
    gapIdx,
    riskId,
    riskDate,
    shortfall: cov.total_shortfall_base,
  }
}

const OPEN_ENDED_YM = '9999-99'

/** The one shared re-simulation primitive: `amount` is added to every month in
 *  [fromYmIncl, toYmExcl) — see the module doc for why this is an EXACT delta, not a
 *  heuristic, for "move a dated event a few months" and "add a one-time inflow" alike. */
function windowedDelta(
  timeline: CoverageTimelineMonth[],
  amount: number,
  fromYmIncl: string,
  toYmExcl: string,
): number[] {
  return timeline.map(
    (m) => m.balance + (m.year_month >= fromYmIncl && m.year_month < toYmExcl ? amount : 0),
  )
}

function simulateWindow(
  timeline: CoverageTimelineMonth[],
  floor: number,
  amount: number,
  fromYmIncl: string,
  toYmExcl: string,
): { afterMin: number; closes: boolean } {
  const shifted = windowedDelta(timeline, amount, fromYmIncl, toYmExcl)
  const afterMin = shifted.length ? Math.min(...shifted) : 0
  const closes = !shifted.some((b) => b < floor)
  return { afterMin, closes }
}

const ym = (date: string) => (date || '').slice(0, 7)
const addDaysIso = (dateStr: string, n: number): string => {
  const d = new Date(dateStr + 'T00:00:00')
  d.setDate(d.getDate() + n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const monthStart = (yyyymm: string) => `${yyyymm}-01`
const weeksBetween = (a: string, b: string) =>
  Math.round(
    (new Date(b + 'T00:00:00').getTime() - new Date(a + 'T00:00:00').getTime()) / (7 * 86400000),
  )

export type ProposalKind = 'move' | 'topup' | 'defer'

const weeksPhrase = (weeks: number): string =>
  weeks <= 0 ? 'less than a week' : weeks === 1 ? '1 week' : `${weeks} weeks`

/** Why a shortfall exists when the money meant to cover it arrives too late: the cause, then the
 *  remedy, each a full sentence. The funding's label is quoted — it is free text ("Sell 255 KSTL
 *  (Jun 2027 vest cleared) for 9th installment") and read bare it ran into the words around it
 *  (persona QA round 7). */
export function lateFundingHint(
  label: string,
  availableDate: string,
  weeks: number,
  isSale: boolean,
): string {
  return (
    `It is funded by "${label}", which does not arrive until ${fmtDate(availableDate)}, ` +
    `about ${weeksPhrase(weeks)} after the payment. ` +
    (isSale ? 'Selling earlier' : 'Pulling that funding forward') +
    ' closes the gap without new borrowing.'
  )
}

/** A concrete, re-simulated fix for a funding gap — mirrors the design handoff's
 *  `buildProposals()` cards exactly (title/body/outcome), but `write` performs a REAL change
 *  through the existing commitments REST endpoints instead of a scripted local mutation
 *  (ADR §3 — routes the actual commit through the real app, not a fake chat surface). */
export interface Proposal {
  id: string
  kind: ProposalKind
  title: string
  body: string
  /** Plain-language summary for the ribbon, one or two full sentences. */
  ribbonHint: string
  /** Prefilled prompt for the REAL Lucius dock's "Fix with Lucius" action. */
  luciusPrompt: string
  afterMin: number
  closes: boolean
  /** Performs the write via `fetch` against the real commitments API. */
  write: () => Promise<void>
}

interface ProposalCtx {
  list: Commitment[]
  cov: CoverageDerived
  fx: Record<string, number>
  patchFunding: (
    commitmentId: string,
    fundingId: string,
    changes: Record<string, unknown>,
  ) => Promise<void>
  patchInstallment: (
    commitmentId: string,
    installmentId: string,
    changes: Record<string, unknown>,
  ) => Promise<void>
  addFunding: (commitmentId: string, source: Record<string, unknown>) => Promise<void>
  addAction: (commitmentId: string, action: Record<string, unknown>) => Promise<void>
}

/** Builds up to 3 concrete fix proposals when a funding gap exists — pull a dated draw
 *  forward, add a one-time top-up, or defer the triggering installment. Each is re-simulated
 *  (exactly, see module doc) so "closes the gap" is computed, never scripted. */
export function buildProposals(ctx: ProposalCtx): Proposal[] {
  const { list, cov, fx } = ctx
  if (cov.gapIdx < 0 || !cov.riskId) return []
  const gapYm = cov.timeline[cov.gapIdx]?.year_month
  if (!gapYm) return []
  const shortfall = cov.shortfall
  const riskC = list.find((c) => c.id === cov.riskId)
  if (!riskC) return []

  const proposals: Proposal[] = []

  // 1 — pull a dated draw forward (lands after the gap, big enough to matter)
  for (const c of list) {
    if (c.status !== 'active' || isTracker(c)) continue
    for (const f of c.funding_plan || []) {
      if (f.disabled || !f.available_date) continue
      if (ym(f.available_date) <= gapYm) continue
      const valueBase = f.value_base ?? toBase(f.amount ?? 0, f.currency, fx)
      if (valueBase < shortfall) continue
      const newDate = addDaysIso(monthStart(gapYm), -14)
      const w = cov.riskDate ? weeksBetween(cov.riskDate, f.available_date) : 0
      const isSale = f.type === 'asset_sale'
      const { afterMin, closes } = simulateWindow(
        cov.timeline,
        cov.floor,
        valueBase,
        ym(newDate),
        ym(f.available_date),
      )
      proposals.push({
        id: 'move-' + f.id,
        kind: 'move',
        title: isSale ? `Sell earlier: bring "${f.label}" forward` : `Bring "${f.label}" forward`,
        body: `It's dated ${fmtDate(f.available_date)}, about ${weeksPhrase(w)} after the ${cov.riskDate ? fmtDate(cov.riskDate) : ''} payment it should cover. Move it to ${fmtDate(newDate)}. No new borrowing, no new cash.`,
        ribbonHint: lateFundingHint(f.label, f.available_date, w, isSale),
        luciusPrompt: `Pull the "${f.label}" funding source on my "${c.name}" commitment forward from ${fmtDate(f.available_date)} to ${fmtDay(newDate)} — it currently lands after a projected cash-floor gap around ${cov.riskDate ? fmtDate(cov.riskDate) : ''}.`,
        afterMin,
        closes,
        write: () => ctx.patchFunding(c.id, f.id, { available_date: newDate }),
      })
    }
  }

  // 2 — one-time top-up transfer, available this week (always available as a fallback)
  const topUp = Math.ceil((shortfall + 2000) / 500) * 500
  const topUpDate = addDaysIso(new Date().toISOString().slice(0, 10), 7)
  {
    const { afterMin, closes } = simulateWindow(
      cov.timeline,
      cov.floor,
      topUp,
      ym(topUpDate),
      OPEN_ENDED_YM,
    )
    proposals.push({
      id: 'topup',
      kind: 'topup',
      title: `Set aside ${fBase(topUp)} now`,
      body: `A one-time transfer from savings, available this week — covers the ${monthLong(gapYm)} dip and keeps you above your ${fBase(cov.floor)} cash floor.`,
      ribbonHint: `A one-time ${fBase(topUp)} transfer this week would cover the dip.`,
      luciusPrompt: `Add a one-time cash top-up of ${fBase(topUp)}, available ${fmtDay(topUpDate)}, to my "${riskC.name}" commitment's funding plan — it closes a projected cash-floor gap around ${monthLong(gapYm)}.`,
      afterMin,
      closes,
      write: () =>
        ctx.addFunding(riskC.id, {
          type: 'cash',
          label: 'One-time top-up',
          amount: topUp,
          currency: getBaseCurrency(),
          available_date: topUpDate,
          note: 'Suggested by Lucius',
        }),
    })
  }

  // 3 — defer the triggering installment until after the next dated inflow
  const trigger = cov.riskDate
    ? riskC.installments.find((i) => i.status !== 'paid' && i.due_date === cov.riskDate)
    : null
  if (trigger && cov.riskDate) {
    const inflows = list
      .filter((c) => c.status === 'active' && !isTracker(c))
      .flatMap((c) => c.funding_plan || [])
      .filter((f) => !f.disabled && f.available_date && f.available_date > cov.riskDate!)
      .sort((a, b) => (a.available_date || '').localeCompare(b.available_date || ''))
    const nextIn = inflows[0]
    if (nextIn?.available_date) {
      const newDue = addDaysIso(nextIn.available_date, 14)
      const cp =
        (riskC.actions || []).map((a) => a.counterparty).find(Boolean) || 'the counterparty'
      const valueBase = toBase(trigger.amount, riskC.currency, fx)
      const { afterMin, closes } = simulateWindow(
        cov.timeline,
        cov.floor,
        valueBase,
        ym(cov.riskDate),
        ym(newDue),
      )
      proposals.push({
        id: 'defer-' + trigger.id,
        kind: 'defer',
        title: `Push the ${trigger.label} to ${fmtDay(newDue)}`,
        body: `Ask ${cp} to move the ${fmtDate(cov.riskDate)} payment until after your ${nextIn.label} lands. I'll add the follow-up action to the checklist.`,
        ribbonHint: `Pushing "${trigger.label}" back until after "${nextIn.label}" arrives would also close it.`,
        luciusPrompt: `Push my "${riskC.name}" commitment's "${trigger.label}" installment (currently due ${fmtDate(cov.riskDate)}) to ${fmtDay(newDue)}, and add a follow-up action to agree the new date with ${cp === 'the counterparty' ? 'the counterparty' : cp}.`,
        afterMin,
        closes,
        write: async () => {
          await ctx.patchInstallment(riskC.id, trigger.id, { due_date: newDue })
          await ctx.addAction(riskC.id, {
            label: `Agree new ${trigger.label} date with ${cp}`,
            category: 'admin',
            due_date: addDaysIso(new Date().toISOString().slice(0, 10), 14),
            status: 'pending',
            counterparty: cp === 'the counterparty' ? '' : cp,
          })
        },
      })
    }
  }

  const order: Record<ProposalKind, number> = { move: 0, topup: 1, defer: 2 }
  proposals.sort((a, b) =>
    a.closes === b.closes ? order[a.kind] - order[b.kind] : a.closes ? -1 : 1,
  )
  return proposals
}
