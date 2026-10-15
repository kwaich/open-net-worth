// plan/useFreshStart.tsx — the "Start fresh from <month>…" kebab action, whole: the stored
// config, the confirm dialog, the ONE write, and its one-shot Undo.
//
// The caller (Plan.tsx) contributes only a menu item (`{ label, onSelect: open }`) and a place
// to render `node`. A fresh start is ONE server transaction that clears the month's assignments
// and moves `budget_config.rollover_start`; it hands back a one-shot TOKEN naming the
// server-held before-image, and Undo posts that token back — no compensating edits computed
// here, and no amounts held client-side to be forged (ADR
// 2026-09-08-budget-fresh-start-moves-the-floor-not-the-data).
//
// "Resume earlier history" (offered when the floor already IS this month) stays the small
// floor-only PUT — it restores no assignments, and its own copy says so.
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { invalidateQueryCache, useQueryCache } from '../../../state/queryCache'
import { FreshStartDialog } from './FreshStartDialog'
import {
  BUDGET_CONFIG_URL,
  fetchBudgetConfig,
  freshStartMenuLabel,
  freshStartToastLabel,
  postFreshStart,
  postFreshStartUndo,
  putRolloverStart,
} from './freshStart'

/** One-shot Undo window, matching the budget table's own undo pill (useBudgetModel). */
const UNDO_MS = 7000

/** Every planning read whose numbers are a function of the carry floor — Ready to Assign, each
 *  envelope's Available, the health strip, the month-nav bound, and the same figures re-folded
 *  by the summary and the engine-neutral lens. Listed rather than prefix-matched on
 *  `/api/planning/` so each entry is a real, checkable route (tests/test_route_path_consistency)
 *  and the reads that DON'T move (envelopes, groups, notes, income) aren't refetched for nothing.
 *
 *  This is a promptness list, not a correctness one: every `useQueryCache` in the app defaults to
 *  `staleTime: 0`, so any other floor-sensitive read revalidates on its own next mount anyway.
 *  Overview's budgeting widgets are the case worth naming — they read planning/summary (already
 *  listed) via `usePlanningSummary`; the "GET /api/overview/budget" their file comments cite is
 *  stale, there is no such backend route. */
const FLOOR_DEPENDENT_READS = [
  '/api/planning/month',
  '/api/planning/health',
  '/api/planning/summary',
  '/api/planning/view',
]

export interface FreshStartAction {
  /** The kebab item's label — past tense once this month IS the floor. */
  label: string
  /** Opens the confirm dialog. */
  open: () => void
  /** The dialog + the Undo pill. Render once, anywhere in the tab. */
  node: ReactNode
}

export function useFreshStart(
  month: string,
  toast: (msg: string) => void,
  /** False while another sub-tab is showing — the action isn't offered there, so don't pay for
   *  its config read on every Plan mount. Cached data (and a pending undo) still render. */
  enabled = true,
): FreshStartAction {
  const configQuery = useQueryCache(BUDGET_CONFIG_URL, fetchBudgetConfig, { enabled })
  const rolloverStart = configQuery.data?.rollover_start ?? null
  const [open, setOpen] = useState(false)
  // `run` is the exact inverse of whichever write armed the pill — the fresh-start snapshot
  // posted back, or the floor-only PUT for a resume. Held as a closure so the pill never has to
  // re-derive which of the two it is undoing.
  const [undo, setUndo] = useState<{ label: string; run: () => Promise<void> } | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // One write per decision: a double-clicked Undo used to fire the inverse twice (harmless
  // server-side — the second is idempotent — but the second reply raced the pill's own teardown).
  const inFlightRef = useRef(false)
  const refreshConfig = configQuery.refresh

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    },
    [],
  )

  /** Run one write, refresh everything it moved, then either arm the Undo pill or consume it. */
  const write = useCallback(
    async (
      perform: () => Promise<{ label: string; run: () => Promise<void> } | null>,
      failure: string,
    ) => {
      if (inFlightRef.current) return
      inFlightRef.current = true
      let armed: { label: string; run: () => Promise<void> } | null
      try {
        armed = await perform()
      } catch (err: unknown) {
        toast(err instanceof Error ? err.message : failure)
        return
      } finally {
        inFlightRef.current = false
      }
      void refreshConfig()
      for (const url of FLOOR_DEPENDENT_READS) invalidateQueryCache(url)
      if (timerRef.current) clearTimeout(timerRef.current)
      setUndo(armed)
      if (!armed) {
        toast('Undone')
        return
      }
      timerRef.current = setTimeout(() => setUndo(null), UNDO_MS)
    },
    [refreshConfig, toast],
  )

  const confirm = useCallback(() => {
    setOpen(false)
    if (rolloverStart === month) {
      // "Resume earlier history" — the floor only. Its Undo puts the floor back; the
      // assignments this month's fresh start cleared are long gone by then, as the copy says.
      const previousFloor = rolloverStart
      void write(async () => {
        await putRolloverStart(null)
        return {
          label: freshStartToastLabel(month, null),
          run: async () => void (await putRolloverStart(previousFloor)),
        }
      }, 'Could not resume earlier history')
      return
    }
    void write(async () => {
      const { token } = await postFreshStart(month)
      return {
        label: freshStartToastLabel(month, month),
        run: () => postFreshStartUndo(token),
      }
    }, 'Could not start fresh')
  }, [month, rolloverStart, write])

  const node = (
    <>
      {open && (
        <FreshStartDialog
          month={month}
          rolloverStart={rolloverStart}
          onCancel={() => setOpen(false)}
          onConfirm={confirm}
        />
      )}
      {undo && (
        <div className="pl-undo mb-anchored" role="status">
          <span>{undo.label}</span>
          <button
            onClick={() => {
              const run = undo.run
              void write(async () => {
                await run()
                return null
              }, 'Could not undo')
            }}
          >
            Undo
          </button>
        </div>
      )}
    </>
  )

  return { label: freshStartMenuLabel(month, rolloverStart), open: () => setOpen(true), node }
}
