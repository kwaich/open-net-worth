// plan/useBudgetModel.ts — adapter: server BudgetMonth → prototype envelope model.
// Wraps useBudget() and exposes the LocalGroup[] model + all mutation callbacks
// that budget.tsx needs. All writes are optimistic (local model first) then async
// to the real API, with refresh() as the rollback path.
import { useState, useEffect, useCallback, useRef } from 'react'
import * as PD from './planData'
import { useUndoPill } from './useUndoPill'
import { authFetch } from '../../../api/client'
import { useBudget, monthLabel as serverMonthLabel, nextMonth } from '../../../hooks/useBudget'
import type { BudgetCategoryRow, BudgetGroup as ServerGroup } from '../../../hooks/useBudget'

// ── Local types: PD types extended with 'custom' cadence (by_date targets) ───
export type LocalCat = Omit<PD.BudgetCategory, 'cad' | 'everyUnit'> & {
  cad: 'weekly' | 'monthly' | 'yearly' | 'custom' | null
  everyUnit?: string
  snoozed?: boolean
  /** P4 envelope rows: the backing envelope ("" for a no-envelope pseudo-row) + every
   *  category folded into this row (`key` == categoryIds[0]; length 1 for a plain row). */
  envelopeId: string
  categoryIds: string[]
  /** Target deadlines (handoff delta #13) — echoed straight from the server target spec. */
  dueWeekday?: number | null
  dueDay?: number | null
  /** Cumulative contributions through this month — the "set aside by [date]" goal's progress
   *  measure (spending-immune), mirrors the server's assigned_to_date. */
  assignedToDate?: number
  /** Credit Card Payments (payment_envelope mode, ADR 2026-07-09) — a payment-tracking state
   *  distinct from the target-based status/needed above (never folded together). Defaults
   *  falsy/zero via mapCat so an ordinary row (server sends nothing new) is unaffected. */
  isPaymentEnvelope?: boolean
  /** The card's real debt as of this month (server `owed`) — NOT derived from the two below. */
  paymentOwed?: number
  availableForPayment?: number
  paymentUnderfunded?: number
}

// ── Ready to Assign as a pickable "envelope" (handoff delta #9) ───────────────
export const RTA_KEY = '__rta'

// ── Auto-assign preview (handoff delta #10) ───────────────────────────────────
export interface AutoPrevStep {
  key: string
  label: string
  color: string
  cur: number
  next: number
  delta: number
}
export interface AutoPrevResult {
  label: string
  steps: AutoPrevStep[]
  total: number
}

// Client modes compute straight from the live model (no round-trip needed); history modes
// hit the existing /auto-assign-preview endpoint. Both map onto the SAME AutoPrevStep shape.
// Labels/grouping match real YNAB's own Auto-Assign menu EXACTLY (owner ask, 2026-09-15,
// verified against a real YNAB screenshot). "Underfunded" funds the targets and THEN each card's
// payment shortfall — YNAB's own total was exactly that sum (side by side, 2026-09-28); see
// `buildClientAutoSteps`. "Top up overspent envelopes" isn't part of real YNAB's list and
// has no UI entry point any more — kept here (not in buildAutoAssignItems/AUTO_MSGS) purely so
// `buildClientAutoSteps`'s locked-card exclusion stays covered by its existing regression test
// (budget.cardPayments.test.tsx) without reinterpreting that test's fixtures.
const CLIENT_AUTO_FNS: Record<string, (c: LocalCat) => number | null> = {
  Underfunded: (c) => (c.status === 'underfunded' ? c.assigned + c.needed : null),
  'Top up overspent envelopes': (c) =>
    c.status === 'overspent' ? c.assigned + -c.available : null,
  'Reset Available Amounts': (c) =>
    Math.abs(c.available) > 0.005 ? Math.round((c.assigned - c.available) * 100) / 100 : null,
  'Reset Assigned Amounts': (c) => (c.assigned !== 0 ? 0 : null),
}
const HISTORY_AUTO_MODES: Record<string, string> = {
  'Assigned Last Month': 'assigned_last_month',
  'Spent Last Month': 'spent_last_month',
  'Average Assigned': 'average_assigned',
  // The backend has supported this mode since day one (`_AUTO_ASSIGN_MODES`,
  // `auto_assign_preview`) — it just never had a frontend entry point.
  'Average Spent': 'average_spent',
}
const AUTO_MSGS: Record<string, string> = {
  Underfunded: 'Funded all targets',
  'Assigned Last Month': "Matched last month's assignments",
  'Spent Last Month': "Assigned last month's spending",
  'Average Assigned': 'Assigned your monthly average',
  'Average Spent': 'Assigned your average spending',
  'Reset Available Amounts': "Reset every envelope's available amount to zero",
  'Reset Assigned Amounts': 'Released all assignments to Ready to Assign',
}

// ── The identity's envelope-side term, per assignment (ADR 2026-09-09) ────────────────────
// Ready to Assign = cash − baseline − held in envelopes − card spending not covered this month.
// `held in envelopes` is the SIGNED Σ of every envelope's Available, so an assignment of `delta`
// moves it by exactly `delta` — no clamp, no per-row case, whether the row was in the black, in
// a hole, or crossing zero. Assigning money moves no cash, so RTA moves by exactly `−delta`.
//
// Covering a hole therefore COSTS Ready to Assign now (ADR 2026-09-09 reverses 2026-09-08's
// "assigning into a hole reserves nothing, so RTA never moves"): an envelope HOLDS its overspend
// until month-end, and assigning there is what pays for it.
//
// `card_spend_not_covered` needs no client-side offset: it is in-month card-spend ACTIVITY, and
// no assignment creates or retires a swipe. The server DOES re-cover past swipes out of a newly
// funded spending envelope, which shifts money from that term into the payment envelope's own
// Available — but the two moves are equal and opposite, so RTA and the popover's identity both
// still close exactly; only the split between the two terms is stale until the next payload.

/** How much MORE the envelopes hold once `delta` lands on one of them — the signed sum moves by
 *  the assignment itself, so this is `delta`, rounded. Kept as a named function because it is
 *  the identity's term, not an arithmetic coincidence. */
export function heldDeltaForAssign(delta: number): number {
  return Math.round(delta * 100) / 100
}

/** How Ready to Assign moves when `delta` is assigned to any envelope row: `−delta`, always. */
export function rtaDeltaForAssign(delta: number): number {
  return -heldDeltaForAssign(delta)
}

// Pure — a client mode's steps, computed straight from the live model (no round-trip). Hidden
// rows are excluded (matches the toolbar's own filterCounts convention); a $0 delta (below the
// 0.5-cent threshold every money op in this file rounds to) never becomes a step.
export function buildClientAutoSteps(cats: LocalCat[], label: string): AutoPrevStep[] {
  const fn = CLIENT_AUTO_FNS[label]
  if (!fn) return []
  const steps: AutoPrevStep[] = []
  for (const c of cats) {
    if (c.hidden) continue
    // Payment envelopes (Credit Card Payments) are out of the target-based bulk machinery
    // entirely (UI spec §6) — never let a bulk auto-assign move money into a locked card row.
    if (c.isPaymentEnvelope) continue
    const nv = fn(c)
    if (nv == null) continue
    const delta = Math.round((nv - c.assigned) * 100) / 100
    if (Math.abs(delta) < 0.005) continue
    steps.push({
      key: c.key,
      label: c.label,
      color: c.color,
      cur: c.assigned,
      next: Math.round(nv * 100) / 100,
      delta,
    })
  }
  if (label === 'Underfunded') steps.push(...cardShortfallSteps(cats))
  return steps
}

// Cards last, each by its own payment shortfall. Safe beside the target steps: those only fund
// rows with Available ≥ 0, whose card swipes are already fully covered, so none of them moves
// money into a card's payment envelope (the server's `_auto_underfunded` holds the same line).
function cardShortfallSteps(cats: LocalCat[]): AutoPrevStep[] {
  return cats
    .filter((c) => c.isPaymentEnvelope && !c.hidden && (c.paymentUnderfunded ?? 0) > 0.005)
    .map((c) => {
      const delta = Math.round((c.paymentUnderfunded ?? 0) * 100) / 100
      const next = Math.round((c.assigned + delta) * 100) / 100
      return { key: c.key, label: c.label, color: c.color, cur: c.assigned, next, delta }
    })
}

// Pure — maps the server /auto-assign-preview response onto the SAME AutoPrevStep shape a
// client mode produces, so the modal renders either kind identically. `colorOf` resolves each
// step's row colour from the live model (the server response carries no colour of its own).
// `hiddenOf` excludes hidden rows (FEB-05 — hidden-row policy must be the SAME for client and
// server-backed modes; buildClientAutoSteps already excludes hidden, this brings history modes
// to parity so a hidden row is never silently written by "Assigned last month" et al).
export function mapServerAutoSteps(
  serverSteps: Array<{
    envelope_id: string
    label: string
    current_assigned: number
    new_assigned: number
    delta: number
  }>,
  colorOf: (key: string) => string,
  hiddenOf: (key: string) => boolean,
): AutoPrevStep[] {
  return serverSteps
    .filter((s) => !hiddenOf(s.envelope_id))
    .map(
      (s): AutoPrevStep => ({
        key: s.envelope_id,
        label: s.label,
        color: colorOf(s.envelope_id),
        cur: s.current_assigned,
        next: s.new_assigned,
        delta: s.delta,
      }),
    )
    .filter((s) => Math.abs(s.delta) >= 0.005)
}
export type LocalGroup = Omit<PD.BudgetGroup, 'cats'> & {
  cats: LocalCat[]
  hidden?: boolean
  synced?: boolean
}

// ── Pure helpers ──────────────────────────────────────────────────────────────
const STABLE_PAL = [
  '#34b08a',
  '#3b86dd',
  '#e0a032',
  '#9a7fd0',
  '#22b8cf',
  '#d4537e',
  '#e2554f',
  '#1d9e75',
  '#bd7a1a',
]
const FACTOR: Record<string, number> = { monthly: 1, yearly: 1 / 12 }

export function stableColor(key: string): string {
  let h = 0
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0
  return STABLE_PAL[Math.abs(h) % STABLE_PAL.length]!
}

// A write's server rejection surfaces its real {detail} rather than a generic message.
function errMsg(err: unknown, fallback = 'Something went wrong'): string {
  return err instanceof Error && err.message ? err.message : fallback
}

function ownColor(row: BudgetCategoryRow, colorMap: Map<string, string>): string | undefined {
  const primaryCat = row.category_ids[0]
  return row.color || (primaryCat ? colorMap.get(primaryCat) : undefined) || undefined
}

// A per-key hash collides often on a 9-colour palette (two cards side by side both green), so
// rows with no colour of their own take the LEAST-used palette colour in display order —
// distinct until the palette is exhausted, and stable across reloads since order is.
export function fallbackColors(
  groups: ServerGroup[],
  colorMap: Map<string, string>,
): Map<string, string> {
  const rows = groups.flatMap((g) => g.categories)
  const uses = new Map<string, number>(STABLE_PAL.map((c) => [c, 0]))
  for (const r of rows) {
    const c = ownColor(r, colorMap)?.toLowerCase()
    if (c && uses.has(c)) uses.set(c, uses.get(c)! + 1)
  }
  const out = new Map<string, string>()
  for (const r of rows) {
    if (ownColor(r, colorMap)) continue
    let pick = STABLE_PAL[0]!
    for (const c of STABLE_PAL) if (uses.get(c)! < uses.get(pick)!) pick = c
    uses.set(pick, uses.get(pick)! + 1)
    out.set(r.key, pick)
  }
  return out
}

export function mapCat(
  row: BudgetCategoryRow,
  colorMap: Map<string, string>,
  hiddenKeys: Set<string>,
  month: string,
  fallback?: Map<string, string>,
): LocalCat {
  // Display fallback chain (handoff item 5 — envelope owns its colour): the envelope's OWN
  // colour first, then the first-member category's colour (pre-split-era 1:1 books, or an
  // envelope that hasn't been given its own colour yet), then a stable hash of the key.
  // Both colorMap/hiddenKeys are keyed by REAL category keys — row.key is the envelope id
  // (docs/adr/2026-07-05-envelope-id-native.md), so look up the row's PRIMARY member instead
  // (undefined for a zero-category row, which falls through exactly like before).
  const primaryCat = row.category_ids[0]
  const color = ownColor(row, colorMap) || fallback?.get(row.key) || stableColor(row.key)
  const hidden = !!primaryCat && hiddenKeys.has(primaryCat)
  let target = 0,
    tt: 'set_aside' | 'refill' | 'balance' | null = null
  let cad: LocalCat['cad'] = null,
    tamt: number | undefined,
    due: string | undefined
  if (row.target) {
    tamt = row.target.amount
    due = row.target.date ?? undefined
    if (row.target.type === 'by_date') {
      // A by_date target is the dated Custom cadence. Its flavor: a 'set_aside' by-date goal
      // stores needs_whole_amount=true (round-trips), everything else is the "Have a balance
      // of $X by [date]" savings goal ('balance'). by_date's MATH ignores the flag either way
      // (always measures Available) — this only preserves the label the user picked.
      cad = 'custom'
      tt = row.target.needs_whole_amount ? 'set_aside' : 'balance'
      target = row.target.amount / PD.monthsUntil(month, due)
    } else {
      // Recurring (weekly/monthly/yearly) — set aside vs. fill up is the needs_whole_amount flag.
      tt = row.target.needs_whole_amount ? 'set_aside' : 'refill'
      if (row.target.type === 'weekly') {
        cad = 'weekly'
        // Matches the server's own validity check (services/planning.py:1396) — an out-of-range
        // or missing due_weekday falls back to 0 (Monday), never silently miscounting.
        const dueWeekday = row.target.due_weekday
        const weekday =
          typeof dueWeekday === 'number' && dueWeekday >= 0 && dueWeekday <= 6 ? dueWeekday : 0
        target = row.target.amount * PD.weeksInMonth(month, weekday)
      } else {
        cad = row.target.type as 'monthly' | 'yearly'
        target = row.target.amount * (FACTOR[row.target.type] ?? 1)
      }
    }
  }
  // Status/needed are SERVER truth (services/planning.needed_this_month) — never re-derived
  // client-side. The old client-only recompute approximated a weekly target's monthly
  // equivalent as amount×52/12 (drifts from the server's real 4-or-5-Monday count) and never
  // honored a snoozed target (server needed_this_month is exactly 0 while snoozed; the old
  // local math kept showing "underfunded" regardless). `overspent`/`overfunded` are the two
  // states the server's funding-status vocabulary doesn't carry — both are still derived here,
  // but from server-truth `available`/`target`, never from re-derived money math.
  const needed = row.needed_this_month
  // A payment envelope (Credit Card Payments) has no target and its own overpaid/underfunded
  // vocabulary — force status 'none' so it NEVER matches the target-based status consumers
  // (filter chips, Summary "overspent" banner, and the money-writing "Top up overspent envelopes" /
  // "Underfunded" bulk auto-assign). Its `available` (== available_for_payment) is
  // legitimately negative when overpaid, which would otherwise read as 'overspent'.
  const status: LocalCat['status'] = row.is_payment_envelope
    ? 'none'
    : row.available < -0.005
      ? 'overspent'
      : !target
        ? 'none'
        : tt === 'refill' && row.available - target > 0.005
          ? 'overfunded'
          : needed > 0.005
            ? 'underfunded'
            : 'funded'
  return {
    key: row.key,
    label: row.label,
    color,
    assigned: row.assigned,
    activity: row.activity,
    available: row.available,
    target,
    tt,
    cad,
    status,
    needed: status === 'underfunded' ? needed : 0,
    hidden,
    tamt,
    due,
    snoozed: row.status === 'snoozed',
    envelopeId: row.envelope_id,
    categoryIds: row.category_ids,
    dueWeekday: row.target?.due_weekday ?? null,
    dueDay: row.target?.due_day ?? null,
    assignedToDate: row.assigned_to_date ?? row.assigned,
    isPaymentEnvelope: !!row.is_payment_envelope,
    paymentOwed: row.owed ?? 0,
    availableForPayment: row.available_for_payment ?? 0,
    paymentUnderfunded: row.underfunded ?? 0,
  }
}

function mapGroup(
  g: ServerGroup,
  colorMap: Map<string, string>,
  hiddenKeys: Set<string>,
  month: string,
  fallback: Map<string, string>,
): LocalGroup {
  const cats = g.categories.map((c) => mapCat(c, colorMap, hiddenKeys, month, fallback))
  // A group's hidden state is DERIVED (every member hidden), never a separately-stored flag —
  // storing it only client-side meant it evaporated on every refresh() (mapGroup never re-set
  // it), so a hidden group reappeared as an empty header after any write. Deriving it from the
  // (server-truth) per-category hidden set self-heals: unhiding any one member un-hides the
  // group too, no separate persistence needed.
  // ENV-RES-05: a zero-category envelope row has NO backing category to hide server-side (it
  // can never carry `hidden: true`), so folding it into the "every member hidden" check meant a
  // group containing even ONE empty envelope could never derive as hidden at all. Derive from
  // category-BACKED members only — an empty-envelope row rides along inert either way.
  const backed = cats.filter((c) => c.categoryIds.length > 0)
  const hidden = backed.length > 0 && backed.every((c) => c.hidden)
  return {
    id: g.id,
    name: g.name,
    collapsed: g.collapsed,
    cats,
    subtotal: g.subtotal,
    synced: g.synced,
    hidden,
  }
}

export function recompute(gs: LocalGroup[]): LocalGroup[] {
  return gs.map((g) => {
    const cats = g.cats.map((c) => {
      // by_date (Custom) rows: the server's status/needed already encode the cumulative rules —
      // "set aside by [date]" measures contributions (spending-immune), "have a balance of"
      // measures Available. Re-deriving from the per-month client approximation would flip an
      // ahead-of-pace goal to "underfunded" on a $0 month, so keep the server truth from mapCat.
      if (c.cad === 'custom' && c.target) return c
      const { status, needed } = PD.statusOf(c.assigned, c.activity, c.available, c.target, c.tt)
      return { ...c, status, needed }
    })
    const subtotal = cats.reduce(
      (a, c) => ({
        assigned: a.assigned + c.assigned,
        activity: a.activity + c.activity,
        available: a.available + c.available,
      }),
      { assigned: 0, activity: 0, available: 0 },
    )
    return { ...g, cats, subtotal }
  })
}

// ── Hook ──────────────────────────────────────────────────────────────────────
export function useBudgetModel() {
  const bud = useBudget()
  const {
    month,
    setMonth,
    data,
    health,
    categories,
    hiddenKeys,
    loading,
    monthSwitching,
    error,
    refresh,
    refreshAll,
    assign,
    move: serverMove,
    setTarget: serverSetTarget,
    createGroup,
    renameGroup: serverRenameGroup,
    deleteGroup: serverDeleteGroup,
    reorderGroups: serverReorderGroups,
    moveCategory: serverMoveCategory,
    reorderCategories: serverReorderCategories,
    renameCategory: serverRenameCategory,
    deleteCategory: serverDeleteCategory,
    hideCategory: serverHideCategory,
    unhideCategory: serverUnhideCategory,
    createEnvelope: serverCreateEnvelope,
    renameEnvelope: serverRenameEnvelope,
    mergeEnvelope: serverMergeEnvelope,
    setEnvelopeColor: serverSetEnvelopeColor,
    deleteEnvelope: serverDeleteEnvelope,
    undoDeleteEnvelope: serverUndoDeleteEnvelope,
    splitCategory: serverSplitCategory,
    addCategoriesToEnvelope: serverAddCategories,
    addCategoryToEnvelope: serverAddCategoryToEnvelope,
    autoAssignPreview,
    notes,
    setNote,
  } = bud

  const [model, setModel] = useState<LocalGroup[]>([])
  // What our un-refetched writes have done to `held_in_envelopes` since the payload landed — the
  // ONE piece of optimistic state behind the RTA breakdown, and the reason the identity closes
  // on EVERY render. Both envelope-side terms derive from this same pair (payload + offset):
  // budget.tsx reads `data.held_in_envelopes + heldOffset`, and `rta` just below reads
  // `data.ready_to_assign − heldOffset`, so the offset cancels out of the subtraction whatever
  // its value. Reset to 0 on every sync, when the server's own figures already include it.
  const [heldOffset, setHeldOffset] = useState(0)
  // Ready to Assign is DERIVED, never a second copy of the number kept in state beside the
  // payload. Held in its own `useState` (written by the sync effect below), it lagged the other
  // four terms by exactly one render every time a payload landed — an effect runs AFTER the
  // render that scheduled it — which fired the breakdown's "does not match its own terms"
  // warning on every ordinary load of a healthy book (QA + UI lanes, 2026-09-09). Deriving it
  // makes that window structurally impossible rather than merely narrow.
  const rta = Math.round(((data?.ready_to_assign ?? 0) - heldOffset) * 100) / 100
  const allCatsRef = useRef<LocalCat[]>([])
  allCatsRef.current = model.flatMap((g) => g.cats)

  /** Apply one write's optimistic effect on every figure the breakdown reads. `deltas` are the
   *  assignment amounts, one per envelope leg — a row's own state doesn't enter into it, so a
   *  step whose row isn't in the model (a hidden row, an archived card's payment envelope)
   *  needs no separate handling. One setter: assigning `delta` moves what the envelopes hold by
   *  `+delta` and Ready to Assign by `−delta`, and `rta` above already reads the second from
   *  the first. */
  const applyOptimisticTerms = useCallback((deltas: number[]) => {
    const dHeld = deltas.reduce((sum, delta) => sum + heldDeltaForAssign(delta), 0)
    if (dHeld === 0) return
    setHeldOffset((o) => Math.round((o + dHeld) * 100) / 100)
  }, [])

  // ── One-shot undo (handoff delta #11) — SERVER-BACKED, shared with 50/30/20 (useUndoPill.ts).
  // Merge is deliberately NOT captured (RFC decision — no exact per-month inverse; manual
  // recovery = restore + Add categories…).
  const { undo, captureUndo, doUndo, clearUndo } = useUndoPill()

  // A raw POST /api/planning/apply — used only where the caller needs a month OTHER than the
  // hook's current one (the delete-envelope undo replays `zeroed_assignments` across every
  // month it touched, not just the current one; `assign()` above is pinned to `month`).
  const applyChange = useCallback(async (change: Record<string, unknown>): Promise<void> => {
    const r = await authFetch('/api/planning/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(change),
    })
    if (!r.ok) {
      let detail = `Request failed (${r.status})`
      try {
        const body = await r.json()
        if (body?.detail) detail = String(body.detail)
      } catch {
        /* non-JSON error body */
      }
      throw new Error(detail)
    }
  }, [])

  // The DERIVED "Uncategorized" group (None-category expense activity) is server-synthesized,
  // never a stored category — it carries no real key, so it's excluded from the editable
  // `model` (which every drag/assign/rename/color affordance assumes has real string keys)
  // and surfaced separately as a plain number for a read-only row.
  const unsortedActivity = data?.groups.find((g) => g.unsorted)?.categories[0]?.activity ?? 0
  // What that bucket HOLDS (ADR 2026-09-10) — cash-only and ≤ 0, so it differs from its
  // Activity whenever the spend was on a card. Read straight off the row, never re-derived.
  const unsortedAvailable = data?.groups.find((g) => g.unsorted)?.categories[0]?.available ?? 0

  // The DERIVED "Unbudgeted" group (RFC envelope⊥category full split) — real, user-labeled
  // categories with activity but no linked envelope. Like Uncategorized, excluded from the editable
  // `model`; surfaced separately (whole group, since each row needs its own key/label/activity
  // for the per-category "Add to envelope…" line — unlike Uncategorized's single rolled-up number).
  const unbudgetedGroup = data?.groups.find((g) => g.unbudgeted)
  const unbudgetedActivity = (unbudgetedGroup?.categories ?? []).reduce((s, c) => s + c.activity, 0)

  // Sync model from server data (reset optimistic state to authoritative) — EXCEPT
  // `collapsed`. That field is its own fire-and-forget optimistic write (toggleCollapse below:
  // flip locally, persist via serverRenameGroup without awaiting it), and this effect reruns on
  // ANY unrelated refetch (a different group's edit, month nav, another tab's write). A refetch
  // landing before that write is reflected server-side echoed the PRE-toggle value here,
  // snapping the group's card open/closed under the user's thumb with no further taps involved
  // (owner-reported "opens and closes on its own"). Carry the group's current LOCAL collapsed
  // state forward across a resync instead — a brand-new group (not in the previous model) still
  // takes the server's value, so a collapse persisted from another session still loads correctly
  // on first paint.
  useEffect(() => {
    if (!data) return
    const colorMap = new Map(categories.map((c) => [c.id, c.color]))
    const editable = data.groups.filter((g) => !g.unsorted && !g.unbudgeted)
    setModel((prev) => {
      const prevCollapsed = new Map(prev.map((g) => [g.id, g.collapsed]))
      const fallback = fallbackColors(editable, colorMap)
      const next = editable.map((g) => mapGroup(g, colorMap, hiddenKeys, data.month, fallback))
      const merged = next.map((g) =>
        prevCollapsed.has(g.id) ? { ...g, collapsed: prevCollapsed.get(g.id)! } : g,
      )
      return recompute(merged)
    })
    // The payload already accounts for every write we made optimistically — drop the offset.
    // `rta` needs no setter: it reads `data.ready_to_assign − heldOffset` directly, so it is
    // already the server's figure the moment this payload is on screen, not one render later.
    setHeldOffset(0)
  }, [data, categories, hiddenKeys])

  // ── Money engine ──────────────────────────────────────────────────────────
  const assignTo = useCallback(
    (key: string, newValue: number, toast?: (msg: string) => void) => {
      const c = allCatsRef.current.find((x) => x.key === key)
      if (!c) return
      const delta = Math.round((newValue - c.assigned) * 100) / 100
      if (Math.abs(delta) < 0.005) return
      const prevAssigned = c.assigned
      setModel((gs) =>
        recompute(
          gs.map((g) => ({
            ...g,
            cats: g.cats.map((x) =>
              x.key !== key
                ? x
                : {
                    ...x,
                    assigned: Math.round((x.assigned + delta) * 100) / 100,
                    available: Math.round((x.available + delta) * 100) / 100,
                  },
            ),
          })),
        ),
      )
      applyOptimisticTerms([delta])
      // Arm the undo pill only AFTER the server confirms (FEB-01 pattern) — a failed assign
      // reverts via refresh() and never offers an undo for a write that never landed.
      assign(key, newValue)
        .then(() =>
          captureUndo(
            delta > 0 ? `Assigned ${PD.fSmart(delta)}` : `Released ${PD.fSmart(-delta)}`,
            () => assign(key, prevAssigned),
          ),
        )
        .catch((err) => {
          refresh()
          toast?.(errMsg(err))
        })
    },
    [assign, refresh, captureUndo, applyOptimisticTerms],
  )

  // ── Auto-assign preview (handoff delta #10) ── EVERY toolbar mode previews before applying:
  // client modes (fund/cover/reset) compute straight from the live model; history modes
  // (assigned/spent last month, average) hit the existing preview endpoint and map its steps.
  // Returns null when there's nothing to do (caller toasts "Nothing to assign", no modal).
  const buildAutoPreview = useCallback(
    async (label: string): Promise<AutoPrevResult | null> => {
      let steps: AutoPrevStep[]
      if (CLIENT_AUTO_FNS[label]) {
        steps = buildClientAutoSteps(allCatsRef.current, label)
      } else {
        const mode = HISTORY_AUTO_MODES[label]
        if (!mode) return null
        const { steps: serverSteps } = await autoAssignPreview(month, mode)
        const colorOf = (key: string) =>
          allCatsRef.current.find((c) => c.key === key)?.color ?? stableColor(key)
        // FEB-05 — hidden rows are excluded from EVERY auto-assign surface, history modes included.
        const hiddenOf = (key: string) =>
          allCatsRef.current.find((c) => c.key === key)?.hidden ?? false
        steps = mapServerAutoSteps(serverSteps, colorOf, hiddenOf)
      }
      if (!steps.length) return null
      const total = Math.round(steps.reduce((sum, s) => sum + s.delta, 0) * 100) / 100
      return { label, steps, total }
    },
    [autoAssignPreview, month],
  )

  // REMOVED, 2026-09-15 (perf regression I shipped the same day): this is where four
  // auto-assign previews were fetched on every month change, purely to print a dollar hint
  // beside the four history modes. Each preview runs a FULL budget context build server-side,
  // the endpoint is a sync def (thread pool, contending for the GIL and the SQLite pool), and
  // the effect's cleanup only discarded the result client-side — an abandoned request from a
  // fast arrow press still ran to completion. Measured on the live server: 229 preview calls
  // in ~16 minutes, peaking at 132 in ONE minute, 4.4s average / 17.3s worst; the process sat
  // at 130% CPU and unrelated endpoints (auth status) degraded to seconds because everything
  // queued behind the pool checkout. Four grey "S$0.00" labels are not worth that.
  // buildAutoAssignItems already renders no hint when the totals are absent, so nothing else
  // changes. If the hints are wanted back, they must come from the month payload the tab
  // ALREADY fetches (one request, one context build) — never a per-mode fan-out from a hook.

  // Apply a CONFIRMED preview verbatim — the steps ARE the plan (no re-derive at confirm time,
  // so what the modal showed is exactly what lands). BUD-SWEEP-01 (frontend half): a SERVER
  // mode (history-based — its preview already came from auto_assign_preview) applies through
  // the SAME single bulk apply_budget_change step the backend uses (never a per-row fan-out —
  // the now-locked assignments store makes this belt-and-braces, but a single request is also
  // just correct); a CLIENT mode's steps only exist locally, so they replay SEQUENTIALLY
  // (await in order) rather than a concurrent Promise.all fan-out. Undo replays every row back
  // to its `cur`, also sequentially. FEB-01: the pill arms ONLY after the forward write
  // actually lands — a failed batch reverts via refresh() and never offers an undo for money
  // that was never moved. refresh() always runs after a successful apply/undo, not only on error.
  const applyAutoPreview = useCallback(
    (prev: AutoPrevResult, toast: (msg: string) => void) => {
      const byKey = new Map(prev.steps.map((s) => [s.key, s]))
      setModel((gs) =>
        recompute(
          gs.map((g) => ({
            ...g,
            cats: g.cats.map((c) => {
              const s = byKey.get(c.key)
              if (!s) return c
              return {
                ...c,
                assigned: Math.round((c.assigned + s.delta) * 100) / 100,
                available: Math.round((c.available + s.delta) * 100) / 100,
              }
            }),
          })),
        ),
      )
      applyOptimisticTerms(prev.steps.map((s) => s.delta))
      toast(AUTO_MSGS[prev.label] ?? 'Applied')
      const serverMode = HISTORY_AUTO_MODES[prev.label]
      const forMonth = month
      void (async () => {
        try {
          if (serverMode) {
            await applyChange({ type: 'auto_assign', month: forMonth, mode: serverMode })
          } else {
            for (const s of prev.steps) await assign(s.key, s.next)
          }
          refresh()
          captureUndo(`Auto-assign • ${PD.fSmart(Math.abs(prev.total))}`, async () => {
            for (const s of prev.steps) await assign(s.key, s.cur)
            refresh()
          })
        } catch (err) {
          refresh()
          toast(errMsg(err))
        }
      })()
    },
    [assign, refresh, captureUndo, applyChange, month, applyOptimisticTerms],
  )

  const moneyOp = useCallback(
    async (key: string, kind: string, amount?: number, toast?: (msg: string) => void) => {
      const c = allCatsRef.current.find((x) => x.key === key)
      if (!c) return
      const A = c.assigned
      const say = (msg: string) => toast?.(msg)
      switch (kind) {
        case 'cover': {
          const amt = -c.available
          if (amt > 0.005) {
            assignTo(key, A + amt, toast)
            // Covering COSTS Ready to Assign now, dollar for dollar (ADR 2026-09-09 reversed the
            // one-day "covering is free" rule) — and the one-click "Cover $X" button fires with
            // no confirm step, by design (it is undoable). So the toast has to be the surface
            // that names the price: lead with what left Ready to Assign, not with the reassuring
            // half. Deliberately NOT a confirm dialog — an extra click on every cover would cost
            // more than the sentence does.
            say('−' + PD.fSmart(amt) + ' from Ready to Assign — ' + c.label + ' is covered')
          }
          break
        }
        case 'fund': {
          const amt = c.needed
          if (amt > 0.005) {
            assignTo(key, A + amt, toast)
            say('Funded ' + c.label + ' • ' + PD.fSmart(amt))
          }
          break
        }
        case 'move': {
          const ex = c.available - (c.target || 0)
          if (ex > 0.005) {
            assignTo(key, A - ex, toast)
            say('Moved ' + PD.fSmart(ex) + ' from ' + c.label + ' to Ready to Assign')
          }
          break
        }
        case 'Underfunded': {
          // The target's own to-go, NOT `c.needed`: that reads 0 once the row is overspent,
          // which made this click cover only the spend and leave the target for a second one.
          const amt = PD.envelopeUnderfunded(PD.targetProgress(c).toGo, c.available)
          if (amt > 0.005) {
            assignTo(key, A + amt, toast)
            say('Assigned ' + PD.fSmart(amt) + ' to ' + c.label)
          }
          break
        }
        case 'Reset Available Amount': {
          if (Math.abs(c.available) > 0.005) {
            assignTo(key, A - c.available, toast)
            say('Reset ' + c.label + ' available to ' + PD.fBase(0))
          }
          break
        }
        case 'Reset Assigned Amount': {
          if (Math.abs(A) > 0.005) {
            assignTo(key, 0, toast)
            say('Reset ' + c.label + ' assigned to ' + PD.fBase(0))
          }
          break
        }
        default: {
          const modeMap: Record<string, string> = {
            'Assigned Last Month': 'assigned_last_month',
            'Spent Last Month': 'spent_last_month',
            'Average Assigned': 'average_assigned',
            'Average Spent': 'average_spent',
          }
          const mode = modeMap[kind]
          if (!mode) break
          try {
            const { steps } = await autoAssignPreview(month, mode)
            const step = steps.find((s) => s.envelope_id === key)
            assignTo(key, step?.new_assigned ?? amount ?? A, toast)
            say(kind + ' for ' + c.label)
          } catch (err) {
            toast?.(errMsg(err, 'Failed to preview auto-assign'))
          }
        }
      }
    },
    [assignTo, month, autoAssignPreview],
  )

  // Envelope↔envelope move money (owner-approved feature B4), extended for handoff delta #9:
  // either end may be the `__rta` sentinel (never both — the picker never offers RTA at both
  // ends since the row itself is excluded from its own destination list). Each ENVELOPE leg
  // moves `held_in_envelopes` by its own amount, so an envelope→envelope move is always
  // RTA-neutral and an `__rta` leg moves the headline by the whole amount — the sentinel is not
  // an envelope, so it contributes no term of its own. One shared server call either way — the
  // backend's `_op_move_money` already understands `__rta` on either end.
  const moveBetweenEnvelopes = useCallback(
    (fromKey: string, toKey: string, amount: number, toast: (msg: string) => void) => {
      const amt = Math.round(amount * 100) / 100
      if (!(amt > 0.005) || fromKey === toKey) return
      const fromIsRta = fromKey === RTA_KEY,
        toIsRta = toKey === RTA_KEY
      if (fromIsRta && toIsRta) return
      const from = fromIsRta ? null : allCatsRef.current.find((x) => x.key === fromKey)
      const to = toIsRta ? null : allCatsRef.current.find((x) => x.key === toKey)
      if ((!fromIsRta && !from) || (!toIsRta && !to)) return
      setModel((gs) =>
        recompute(
          gs.map((g) => ({
            ...g,
            cats: g.cats.map((x) =>
              !fromIsRta && x.key === fromKey
                ? {
                    ...x,
                    assigned: Math.round((x.assigned - amt) * 100) / 100,
                    available: Math.round((x.available - amt) * 100) / 100,
                  }
                : !toIsRta && x.key === toKey
                  ? {
                      ...x,
                      assigned: Math.round((x.assigned + amt) * 100) / 100,
                      available: Math.round((x.available + amt) * 100) / 100,
                    }
                  : x,
            ),
          })),
        ),
      )
      // Both ENVELOPE legs — an `__rta` end has no envelope term, so it is simply absent and
      // the other leg moves the headline on its own.
      applyOptimisticTerms([...(from ? [-amt] : []), ...(to ? [amt] : [])])
      const toastMsg = toIsRta
        ? `Moved ${PD.fSmart(amt)} from ${from!.label} to Ready to Assign`
        : fromIsRta
          ? `Assigned ${PD.fSmart(amt)} from Ready to Assign to ${to!.label}`
          : `Moved ${PD.fSmart(amt)} from ${from!.label} to ${to!.label}`
      toast(toastMsg)
      // FEB-01: capture-THEN-confirm — arming the pill before the server responds meant a
      // REFUSED move (e.g. releasing more than is assigned) still left a "working" undo whose
      // inverse (a delta move with no cap on the from-RTA leg) fabricated a real money move the
      // user never asked for. Only the server's OK arms the inverse; a refusal reverts via
      // refresh() and offers no undo for a write that never landed.
      serverMove(fromKey, toKey, amt)
        .then(() => captureUndo(`Moved ${PD.fSmart(amt)}`, () => serverMove(toKey, fromKey, amt)))
        .catch((err) => {
          refresh()
          toast(errMsg(err))
        })
    },
    [serverMove, refresh, captureUndo, applyOptimisticTerms],
  )

  // ── Target CRUD ───────────────────────────────────────────────────────────
  // Target deadlines (handoff delta #13, display-only): `dueWeekday` only makes sense (and is
  // only SENT) for a weekly target, `dueDay` for monthly, `yDue` (the annual deadline) for
  // yearly — sending the wrong pair for the saved cadence would 422 server-side (TargetBody
  // refuses a mismatched pair), so each is gated on `t.cad` here, not left to the caller.
  const saveTarget = useCallback(
    (
      key: string,
      t: {
        target: number
        tamt: number
        tt: string
        cad: string
        due: string
        rep: boolean
        every: number
        everyUnit: string
        dueWeekday?: number
        dueDay?: number
        yDue?: string
      },
      toast: (msg: string) => void,
    ) => {
      const due = t.cad === 'yearly' ? t.yDue : t.due
      setModel((gs) =>
        recompute(
          gs.map((g) => ({
            ...g,
            cats: g.cats.map((c) =>
              c.key !== key
                ? c
                : {
                    ...c,
                    target: t.target,
                    tt: t.tt as 'set_aside' | 'refill' | 'balance',
                    cad: t.cad as LocalCat['cad'],
                    tamt: t.tamt,
                    due,
                    rep: t.rep,
                    every: t.every,
                    everyUnit: t.everyUnit as LocalCat['everyUnit'],
                    dueWeekday: t.cad === 'weekly' ? (t.dueWeekday ?? null) : null,
                    dueDay: t.cad === 'monthly' ? (t.dueDay ?? null) : null,
                  },
            ),
          })),
        ),
      )
      toast('Target saved')
      const type = t.cad === 'custom' ? 'by_date' : (t.cad as 'weekly' | 'monthly' | 'yearly' | '')
      const date = t.cad === 'custom' ? t.due : t.cad === 'yearly' ? t.yDue : undefined
      serverSetTarget(
        key,
        type,
        t.tamt,
        date,
        undefined,
        t.tt === 'set_aside',
        t.cad === 'weekly' ? t.dueWeekday : undefined,
        t.cad === 'monthly' ? t.dueDay : undefined,
      ).catch((err) => {
        refresh()
        toast(errMsg(err))
      })
    },
    [serverSetTarget, refresh],
  )

  // Snooze (or un-snooze) a category's target for the current month — persists snooze_until
  // while PRESERVING the existing target amount/cadence (re-sends them so the PUT is non-destructive).
  const snoozeTarget = useCallback(
    (cat: LocalCat, snooze: boolean, toast: (msg: string) => void) => {
      const type = cat.cad === 'custom' ? 'by_date' : (cat.cad ?? '')
      const amount = cat.tamt ?? cat.target ?? 0
      if (!type || amount <= 0) {
        toast('Set a target first')
        return
      }
      setModel((gs) =>
        gs.map((g) => ({
          ...g,
          cats: g.cats.map((c) => (c.key === cat.key ? { ...c, snoozed: snooze } : c)),
        })),
      )
      toast(snooze ? cat.label + ' target snoozed this month' : cat.label + ' target un-snoozed')
      serverSetTarget(
        cat.key,
        type as 'weekly' | 'monthly' | 'yearly' | 'by_date',
        amount,
        cat.cad === 'custom' ? cat.due : undefined,
        snooze ? month : null,
        cat.tt === 'set_aside',
      ).catch((err) => {
        refresh()
        toast(errMsg(err))
      })
    },
    [serverSetTarget, refresh, month],
  )

  const removeTarget = useCallback(
    (key: string, toast: (msg: string) => void) => {
      setModel((gs) =>
        recompute(
          gs.map((g) => ({
            ...g,
            cats: g.cats.map((c) =>
              c.key !== key
                ? c
                : {
                    ...c,
                    target: 0,
                    tt: null,
                    cad: null,
                    tamt: undefined,
                    due: undefined,
                    rep: false,
                    every: undefined,
                  },
            ),
          })),
        ),
      )
      toast('Target removed')
      serverSetTarget(key, '', 0).catch((err) => {
        refresh()
        toast(errMsg(err))
      })
    },
    [serverSetTarget, refresh],
  )

  // ── Envelope create (RFC envelope⊥category full split; handoff item 4 — the New-envelope
  // modal replaces the inline add-row) ────────────────────
  // "Add envelope" (group + / group-menu, now via NewEnvelopeModal): a pure envelope carrying
  // its own colour, optionally created WITH a checklist of still-unlinked categories folded in
  // (each linked via add-category right after creation — zero picks = a pure empty envelope,
  // unchanged from the P4 baseline). The optimistic row is a zero-category envelope
  // (categoryIds: [], key === its own temp id, same convention the server uses — RFC
  // §Backend contract item 3, "row identity = envelope id") so the row renders instantly;
  // refresh() (fired inside every network call below) reconciles the temp id with the real one
  // and folds in the picked categories once they've linked.
  const addEnvelopeLocal = useCallback(
    (
      gid: string,
      label: string,
      color: string,
      categoryIds: string[],
      toast?: (msg: string) => void,
    ) => {
      const tmpId = 'env_tmp_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
      setModel((gs) =>
        recompute(
          gs.map((g) =>
            g.id !== gid
              ? g
              : {
                  ...g,
                  cats: [
                    {
                      key: tmpId,
                      label,
                      color,
                      assigned: 0,
                      activity: 0,
                      available: 0,
                      target: 0,
                      status: 'none' as const,
                      needed: 0,
                      tt: null,
                      cad: null,
                      envelopeId: tmpId,
                      categoryIds: [],
                    },
                    ...g.cats,
                  ],
                },
          ),
        ),
      )
      ;(async () => {
        try {
          const env = await serverCreateEnvelope(label, gid, color)
          for (const key of categoryIds) await serverAddCategoryToEnvelope(env.id, key)
          const labels = categoryIds.map(
            (k) => categories.find((c) => c.id === k)?.label ?? 'Uncategorized',
          )
          toast?.(
            labels.length
              ? `Envelope “${label}” created — funds ${labels.join(', ')}`
              : `Envelope “${label}” created — link categories from its ⋯ menu`,
          )
        } catch (err) {
          refresh()
          toast?.(errMsg(err))
        }
      })()
    },
    [serverCreateEnvelope, serverAddCategoryToEnvelope, refresh, categories],
  )

  // Row dot recolor (handoff item 5 — envelope owns its colour): PATCHes the ENVELOPE, not the
  // category — a zero-category envelope becomes recolorable too (it had no backing category to
  // recolor against before). The Manage-categories dot (categoriesManager.tsx, a separate call
  // site) keeps editing the category's own colour, untouched here.
  const recolorEnvelope = useCallback(
    (envelopeId: string, color: string, toast: (msg: string) => void) => {
      setModel((gs) =>
        gs.map((g) => ({
          ...g,
          cats: g.cats.map((c) => (c.envelopeId === envelopeId ? { ...c, color } : c)),
        })),
      )
      toast('Color updated')
      serverSetEnvelopeColor(envelopeId, color).catch((err) => {
        refresh()
        toast(errMsg(err, 'Failed to update color'))
      })
    },
    [serverSetEnvelopeColor, refresh],
  )

  // ── Envelope lifecycle: delete (ADR 2026-09-26-deleting-an-envelope-deletes-it) ───────────
  // Toast copies ported verbatim from prototype/plan/budget.jsx deleteEnvelope/restoreEnvelope.
  // The delete is now for REAL (no more archive-and-restore) — its Undo replays the
  // SERVER-HELD before-image named by `result.undo_token` in ONE call; the client no longer
  // replays `zeroed_assignments` itself (the server does it, id/target/note/cap included).
  // The TOAST figure is the server's own realized `delta_rta`/`available` for the CURRENT month
  // (`result`, echoed by the DELETE response itself) — never a client-precomputed `available`,
  // which undercounts: the whole assigned amount releases, only the uncovered share of a cash
  // hole stays held elsewhere (docs/launch/BOARD.md's undercounting repro).
  const deleteEnvelope = useCallback(
    (envelopeId: string, envelopeName: string, toast: (msg: string) => void) => {
      return serverDeleteEnvelope(envelopeId, month)
        .then((result) => {
          toast(
            `Envelope “${envelopeName}” deleted` +
              // Deleting an envelope drops its row out of `held_in_envelopes`, releasing a
              // positive balance to Ready to Assign. A hole does NOT die with the envelope
              // (ADR 2026-09-10): the detached categories are unlinked, and unlinked cash
              // spend holds the same hole in `held_outside_envelopes` until the month-end
              // roll — so Ready to Assign is not charged now. Same fact the confirmation
              // modal states before the click (`DeleteEnvelopeModal`).
              (result.delta_rta > 0.005
                ? ` — ${PD.fSmart(result.delta_rta)} returned to Ready to Assign`
                : result.available < -0.005
                  ? ` — its ${PD.fSmart(-result.available)} overspend is still held until month-end`
                  : ''),
          )
          captureUndo('Envelope deleted', async () => {
            await serverUndoDeleteEnvelope(result.undo_token)
            refresh()
          })
          return result
        })
        .catch((err) => {
          toast(errMsg(err, 'Failed to delete envelope'))
          throw err
        })
    },
    [serverDeleteEnvelope, serverUndoDeleteEnvelope, refresh, captureUndo, month],
  )

  // ── Category CRUD ─────────────────────────────────────────────────────────
  const renameCat = useCallback(
    (key: string, label: string, toast?: (msg: string) => void) => {
      setModel((gs) =>
        gs.map((g) => ({ ...g, cats: g.cats.map((c) => (c.key === key ? { ...c, label } : c)) })),
      )
      serverRenameCategory(key, label).catch((err) => {
        refresh()
        toast?.(errMsg(err))
      })
    },
    [serverRenameCategory, refresh],
  )

  // Optimistic remove → server delete. On a 409 (the category is referenced by transactions
  // OUTSIDE the current month, which the caller's fast-path check can't see) the write reverts
  // and rethrows so the caller (budget.tsx requestDeleteCat) can recover into the reassign
  // modal instead of a silent revert with no explanation.
  const deleteCat = useCallback(
    (
      key: string,
      reassignLabel?: string,
      reassignToKey?: string,
      toast?: (msg: string) => void,
    ) => {
      setModel((gs) =>
        recompute(gs.map((g) => ({ ...g, cats: g.cats.filter((c) => c.key !== key) }))),
      )
      toast?.(
        reassignLabel
          ? 'Category deleted — activity moved to ' + reassignLabel
          : 'Category deleted',
      )
      return serverDeleteCategory(key, !!reassignToKey, reassignToKey).catch((err) => {
        refresh()
        toast?.(errMsg(err))
        throw err
      })
    },
    [serverDeleteCategory, refresh],
  )

  // ── Envelope lifecycle (P4 — merge/split/rename) ──────────────────────────────
  // Non-optimistic: fire → toast → refresh() (spec §3.3) — a merge/split reshapes which
  // rows exist, not just their numbers, so optimistic local patching isn't worth the risk.
  const renameEnvelope = useCallback(
    (envelopeId: string, name: string, toast: (msg: string) => void) => {
      serverRenameEnvelope(envelopeId, name)
        .then(() => toast('Envelope renamed'))
        .catch((err) => toast(errMsg(err, 'Failed to rename envelope')))
    },
    [serverRenameEnvelope],
  )

  const mergeEnvelope = useCallback(
    (
      sourceEnvelopeId: string,
      targetEnvelopeId: string,
      sourceName: string,
      targetName: string,
      toast: (msg: string) => void,
    ) => {
      serverMergeEnvelope(sourceEnvelopeId, targetEnvelopeId)
        .then(() => toast(`Merged ${sourceName} into ${targetName}`))
        .catch((err) => toast(errMsg(err, 'Failed to merge envelopes')))
    },
    [serverMergeEnvelope],
  )

  // Unlink (handoff item 1 — copy CHANGED: no longer "split into its own envelope", since
  // unlinking no longer mints one — the category becomes unbudgeted instead). Undo re-links
  // the SAME category back onto the SAME envelope it came from — always a clean inverse (the
  // envelope survives an unlink untouched, possibly zero-category).
  const splitCategory = useCallback(
    (
      envelopeId: string,
      categoryId: string,
      categoryLabel: string,
      envelopeName: string,
      toast: (msg: string) => void,
    ) => {
      serverSplitCategory(envelopeId, categoryId)
        .then(() => {
          // ENV-RES-06 — Unbudgeted only surfaces a category with THIS-MONTH activity, so a
          // dormant unlinked category (an annual bill, say) can vanish from view entirely; point
          // at the two real re-link paths instead of promising a row that may not exist.
          toast(
            `${categoryLabel} unlinked from ${envelopeName} — link it again via any envelope's Add categories…, or from Manage categories`,
          )
          captureUndo(`${categoryLabel} unlinked`, () =>
            serverAddCategoryToEnvelope(envelopeId, categoryId),
          )
        })
        .catch((err) => toast(errMsg(err, 'Failed to remove category')))
    },
    [serverSplitCategory, serverAddCategoryToEnvelope, captureUndo],
  )

  // "Add categories…" — the envelope-side direction (owner, 2026-07-03): stand on an
  // envelope, pull N categories in. Composition of the hardened split/merge ops in the hook.
  // Undo scope (RFC decision — see `.claude/rfcs/budget-envelopes-handoff-port.md` §B11): a
  // non-sole pick is a clean unlink+link (inverse = split from target, re-link to its original
  // source envelope); a SOLE-member pick folded its whole source envelope in via MERGE, which
  // has no exact per-month inverse (same reason merge itself isn't one-shot-undoable) — if ANY
  // pick in this batch was a sole-member merge, the whole op is left WITHOUT an undo pill
  // rather than silently under-reverting.
  const addCategoriesToEnvelope = useCallback(
    (
      targetEnvelopeId: string,
      targetName: string,
      picks: Array<{ categoryId: string; sourceEnvelopeId: string; soleMember: boolean }>,
      toast: (msg: string) => void,
    ) => {
      return serverAddCategories(targetEnvelopeId, picks)
        .then(() => {
          toast(
            `Added ${picks.length} ${picks.length === 1 ? 'category' : 'categories'} to ${targetName}`,
          )
          if (!picks.some((p) => p.soleMember)) {
            captureUndo('Categories added', async () => {
              for (const p of picks) {
                await serverSplitCategory(targetEnvelopeId, p.categoryId)
                // One that came from outside every envelope goes back there: nothing to re-link.
                if (p.sourceEnvelopeId)
                  await serverAddCategoryToEnvelope(p.sourceEnvelopeId, p.categoryId)
              }
            })
          }
        })
        .catch((err) => toast(errMsg(err, 'Failed to add categories')))
    },
    [serverAddCategories, serverSplitCategory, serverAddCategoryToEnvelope, captureUndo],
  )

  // "Add to envelope…" from the Unbudgeted surface — a single category with NO envelope yet,
  // linked directly onto an existing one (the AddCategoriesModal flow inverted: single
  // category → pick ONE target envelope).
  const linkCategoryToEnvelope = useCallback(
    (
      categoryId: string,
      targetEnvelopeId: string,
      categoryLabel: string,
      targetName: string,
      toast: (msg: string) => void,
    ) => {
      return serverAddCategoryToEnvelope(targetEnvelopeId, categoryId)
        .then(() => {
          // CONF-7 — prototype copy (budget.jsx:1269).
          toast(`${categoryLabel} now draws from ${targetName}`)
          captureUndo(`${categoryLabel} linked`, () =>
            serverSplitCategory(targetEnvelopeId, categoryId),
          )
        })
        .catch((err) => toast(errMsg(err, 'Failed to add to envelope')))
    },
    [serverAddCategoryToEnvelope, serverSplitCategory, captureUndo],
  )

  // Hide/unhide is a CATEGORY flag server-side (`/api/categories/{key}/hide`) — every call
  // site below must resolve the row to its PRIMARY category key (never `c.key`/`key`, the
  // envelope id) and skip a zero-category row (nothing to hide there); the kebab's own "Hide"
  // item is already gated the same way (budgetEnvelopeOps.tsx).
  const toggleHide = useCallback(
    (key: string, toast?: (msg: string) => void) => {
      const c = allCatsRef.current.find((x) => x.key === key)
      const catKey = c?.categoryIds[0]
      if (!c || !catKey) return
      setModel((gs) =>
        gs.map((g) => ({
          ...g,
          cats: g.cats.map((x) => (x.key === key ? { ...x, hidden: !x.hidden } : x)),
        })),
      )
      const req = c.hidden ? serverUnhideCategory(catKey) : serverHideCategory(catKey)
      req.catch((err) => {
        refresh()
        toast?.(errMsg(err))
      })
    },
    [serverHideCategory, serverUnhideCategory, refresh],
  )

  const unhideGroup = useCallback(
    (gid: string, toast?: (msg: string) => void) => {
      setModel((gs) =>
        gs.map((g) =>
          g.id !== gid
            ? g
            : { ...g, hidden: false, cats: g.cats.map((c) => ({ ...c, hidden: false })) },
        ),
      )
      // unhide all cats in the group
      const g = model.find((x) => x.id === gid)
      if (g) {
        const catKeys = g.cats
          .filter((c) => c.hidden && c.categoryIds[0])
          .map((c) => c.categoryIds[0]!)
        Promise.all(catKeys.map((k) => serverUnhideCategory(k))).catch((err) => {
          refresh()
          toast?.(errMsg(err))
        })
      }
    },
    [model, serverUnhideCategory, refresh],
  )

  const unhideOneFromGroup = useCallback(
    (gid: string, key: string, toast?: (msg: string) => void) => {
      setModel((gs) =>
        gs.map((g) =>
          g.id !== gid
            ? g
            : { ...g, hidden: false, cats: g.cats.map((c) => ({ ...c, hidden: c.key !== key })) },
        ),
      )
      const catKey = allCatsRef.current.find((x) => x.key === key)?.categoryIds[0]
      if (catKey)
        serverUnhideCategory(catKey).catch((err) => {
          refresh()
          toast?.(errMsg(err))
        })
    },
    [serverUnhideCategory, refresh],
  )

  const unhideAll = useCallback(
    (toast: (msg: string) => void) => {
      const hiddenCatKeys = allCatsRef.current
        .filter((c) => c.hidden && c.categoryIds[0])
        .map((c) => c.categoryIds[0]!)
      setModel((gs) =>
        gs.map((g) => ({
          ...g,
          hidden: false,
          cats: g.cats.map((c) => ({ ...c, hidden: false })),
        })),
      )
      toast('All hidden envelopes restored') // CONF-7 — prototype copy (budget.jsx:1174), post-rename
      Promise.all(hiddenCatKeys.map((k) => serverUnhideCategory(k))).catch((err) => {
        refresh()
        toast(errMsg(err))
      })
    },
    [serverUnhideCategory, refresh],
  )

  // ── Group CRUD ────────────────────────────────────────────────────────────
  const addGroup = useCallback(
    (name: string, toast: (msg: string) => void) => {
      const tmpId = 'g' + Date.now()
      setModel((gs) =>
        recompute([
          {
            id: tmpId,
            name,
            collapsed: false,
            cats: [],
            subtotal: { assigned: 0, activity: 0, available: 0 },
          },
          ...gs,
        ]),
      )
      toast('Group “' + name + '” added')
      createGroup(name).catch((err) => {
        refresh()
        toast(errMsg(err))
      })
    },
    [createGroup, refresh],
  )

  const renameGroup = useCallback(
    (gid: string, name: string, toast?: (msg: string) => void) => {
      setModel((gs) => gs.map((g) => (g.id === gid ? { ...g, name } : g)))
      serverRenameGroup(gid, name).catch((err) => {
        refresh()
        toast?.(errMsg(err))
      })
    },
    [serverRenameGroup, refresh],
  )

  const hideGroup = useCallback(
    (gid: string, toast: (msg: string) => void) => {
      const g = model.find((x) => x.id === gid)
      if (!g) return
      // ENV-RES-05: a zero-category envelope row has no backing category — POSTing
      // /api/categories/{envelopeId}/hide 404s (envelopeId isn't a store key), which used to
      // abort the whole Promise.all mid-flight and half-hide the group. Skip those rows; if the
      // WHOLE group is zero-category envelopes, there's nothing to hide at all — say so.
      const backed = g.cats.filter((c) => !c.hidden && c.categoryIds.length > 0)
      if (
        g.cats.length > 0 &&
        backed.length === 0 &&
        g.cats.every((c) => c.categoryIds.length === 0)
      ) {
        toast(`"${g.name}" has no categories yet — link one before hiding the group`)
        return
      }
      setModel((gs) => gs.map((x) => (x.id === gid ? { ...x, hidden: true } : x)))
      toast('Group “' + g.name + '” hidden')
      // Persist by hiding every visible, category-backed member server-side (the agreed
      // semantics for group-hide — the group reappears on unhide via unhideGroup). `c.key` is
      // the envelope id (docs/adr/2026-07-05-envelope-id-native.md) — the hide endpoint needs
      // the row's PRIMARY category key, which `backed`'s own filter guarantees exists.
      Promise.all(backed.map((c) => serverHideCategory(c.categoryIds[0]!))).catch((err) => {
        refresh()
        toast(errMsg(err))
      })
    },
    [model, serverHideCategory, refresh],
  )

  const deleteGroupConfirmed = useCallback(
    (gid: string, targetLabel: string | null, toast: (msg: string) => void) => {
      setModel((gs) => recompute(gs.filter((g) => g.id !== gid)))
      toast(targetLabel ? 'Group deleted — activity moved to ' + targetLabel : 'Group deleted')
      serverDeleteGroup(gid).catch((err) => {
        refresh()
        toast(errMsg(err))
      })
    },
    [serverDeleteGroup, refresh],
  )

  const toggleCollapse = useCallback(
    (gid: string) => {
      setModel((gs) => gs.map((g) => (g.id === gid ? { ...g, collapsed: !g.collapsed } : g)))
      const g = model.find((x) => x.id === gid)
      if (g) serverRenameGroup(gid, undefined, !g.collapsed).catch(() => {})
    },
    [model, serverRenameGroup],
  )

  // ── Drag-and-drop reorder (optimistic; server sync on drop) ──────────────
  // `index` is always sent (append is the explicit `cats.length`, never omitted) so a
  // cross-group drop lands at the dropped slot rather than always appending to the end.
  const moveCatBetween = useCallback(
    (key: string, toGid: string, index: number | null, toast?: (msg: string) => void) => {
      let at = 0
      setModel((gs) => {
        let cat: LocalCat | undefined
        gs.forEach((g) => {
          const f = g.cats.find((x) => x.key === key)
          if (f) cat = f
        })
        if (!cat) return gs
        let ng = gs.map((g) =>
          g.cats.some((x) => x.key === key)
            ? { ...g, cats: g.cats.filter((x) => x.key !== key) }
            : g,
        )
        ng = ng.map((g) => {
          if (g.id !== toGid) return g
          const cats = [...g.cats]
          at = index == null ? cats.length : Math.max(0, Math.min(index, cats.length))
          cats.splice(at, 0, cat!)
          return { ...g, cats }
        })
        return recompute(ng)
      })
      serverMoveCategory(key, toGid, at).catch((err) => {
        refresh()
        toast?.(errMsg(err))
      })
    },
    [serverMoveCategory, refresh],
  )

  const dropCatOnRow = useCallback(
    (
      drag: { gid: string; key: string },
      targetGid: string,
      targetKey: string,
      ov: { edge: string } | null,
      toast?: (msg: string) => void,
    ) => {
      if (drag.key === targetKey) return
      let newOrder: { groupId: string; keys: string[]; index: number } | null = null
      setModel((gs) => {
        let cat: LocalCat | undefined
        gs.forEach((g) => {
          const f = g.cats.find((x) => x.key === drag.key)
          if (f) cat = f
        })
        if (!cat) return gs
        let ng = gs.map((g) =>
          g.cats.some((x) => x.key === drag.key)
            ? { ...g, cats: g.cats.filter((x) => x.key !== drag.key) }
            : g,
        )
        ng = ng.map((g) => {
          if (g.id !== targetGid) return g
          const idx = g.cats.findIndex((x) => x.key === targetKey)
          const at = Math.max(0, ov?.edge === 'bottom' ? idx + 1 : idx)
          const cats = [...g.cats]
          cats.splice(at, 0, cat!)
          newOrder = { groupId: targetGid, keys: cats.map((c) => c.key), index: at }
          return { ...g, cats }
        })
        return recompute(ng)
      })
      // persist: if same group → reorder; if cross-group → moveCategory AT the dropped slot.
      // `newOrder` is mutated inside the setModel closure, invisible to TS control-flow analysis
      // (it narrows the `= null` init to `null`, collapsing the truthy checks to `never`).
      // Re-widen once to the declared type so both branches narrow correctly.
      const pendingOrder = newOrder as { groupId: string; keys: string[]; index: number } | null
      if (drag.gid === targetGid) {
        if (pendingOrder) {
          const { groupId, keys } = pendingOrder
          serverReorderCategories(groupId, keys).catch((err) => {
            refresh()
            toast?.(errMsg(err))
          })
        }
      } else if (pendingOrder) {
        serverMoveCategory(drag.key, targetGid, pendingOrder.index).catch((err) => {
          refresh()
          toast?.(errMsg(err))
        })
      }
    },
    [serverMoveCategory, serverReorderCategories, refresh],
  )

  const reorderGroupBefore = useCallback(
    (srcGid: string, targetGid: string, toast?: (msg: string) => void) => {
      if (srcGid === targetGid) return
      let newOrder: string[] = []
      setModel((gs) => {
        const src = gs.find((g) => g.id === srcGid)
        if (!src) return gs
        const rest = gs.filter((g) => g.id !== srcGid)
        const ti = rest.findIndex((g) => g.id === targetGid)
        rest.splice(ti < 0 ? rest.length : ti, 0, src)
        newOrder = rest.map((g) => g.id)
        return rest
      })
      if (newOrder.length)
        serverReorderGroups(newOrder).catch((err) => {
          refresh()
          toast?.(errMsg(err))
        })
    },
    [serverReorderGroups, refresh],
  )

  // FEB-08: an undo pill's inverse always correctly targets the MONTH it was captured in, but
  // its trailing refresh (via useBudget's month-pinned `refresh`) does not — navigating months
  // then clicking Undo repainted the table with the CAPTURED (now off-screen) month's rows
  // under the CURRENT month's header. Simplest correct fix: the pill can't survive a month
  // change at all — clear it the instant the user navigates.

  const onPrev = useCallback(() => {
    clearUndo()
    const [y, m] = month.split('-').map(Number) as [number, number]
    setMonth(m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}`)
  }, [month, setMonth, clearUndo])

  const onNext = useCallback(() => {
    clearUndo()
    setMonth(nextMonth(month))
  }, [month, setMonth, clearUndo])

  return {
    month,
    monthLabel: serverMonthLabel(month),
    data,
    health,
    loading,
    monthSwitching,
    error,
    refresh,
    refreshAll,
    model,
    setModel,
    rta,
    heldOffset,
    unsortedActivity,
    unsortedAvailable,
    unbudgetedGroup,
    unbudgetedActivity,
    categories,
    hiddenKeys,
    onPrev,
    onNext,
    assignTo,
    moneyOp,
    buildAutoPreview,
    applyAutoPreview,
    moveBetweenEnvelopes,
    saveTarget,
    removeTarget,
    snoozeTarget,
    addEnvelopeLocal,
    renameCat,
    recolorEnvelope,
    deleteCat,
    toggleHide,
    unhideGroup,
    unhideOneFromGroup,
    unhideAll,
    addGroup,
    renameGroup,
    hideGroup,
    deleteGroupConfirmed,
    toggleCollapse,
    moveCatBetween,
    dropCatOnRow,
    reorderGroupBefore,
    renameEnvelope,
    mergeEnvelope,
    splitCategory,
    addCategoriesToEnvelope,
    linkCategoryToEnvelope,
    deleteEnvelope,
    notes,
    setNote,
    undo,
    doUndo,
  }
}
