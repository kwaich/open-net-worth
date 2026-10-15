// plan/useOutlookSim.ts — drives the Outlook from the backend retirement engine (outlookApi).
// Two guarantees keep a fast slider drag smooth:
//   • IDLE-COALESCING — at most one fan request is in flight; rapid changes collapse to the
//     latest, so the server never works through a backlog of stale levers.
//   • PARTIAL MERGE — a drag request returns only the live fan (+rbd), quick (fewer paths); the
//     hook merges it over the last full bundle, so the insights (safe-to-retire age, the "+X/mo"
//     hint, the FIRE-number age) persist until the drag settles (full=true) and they refresh.

import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchInsights, fetchOutlook } from './outlookApi'
import type { OutlookInsights, OutlookLevers } from './outlookApi'
import type { LifeResult, OutcomeRow } from './planData'

export interface SimBundle extends OutlookInsights {
  result: LifeResult
  rbdData: OutcomeRow[] | null
}

export interface SimRequest {
  levers: OutlookLevers
  /** 'rbd' also asks for the Rich/Broke/Dead split. */
  chartMode: string
  /** False while dragging: the quick fan only, no insights until the drag settles. */
  full: boolean
}

const NO_INSIGHTS: OutlookInsights = {
  earliest: null,
  extraSave: null,
  gapSave: null,
  numberAge: null,
}

export function useOutlookSim(
  req: SimRequest,
  key: string,
  planKey: string,
  enabled: boolean,
): {
  bundle: SimBundle | null
  /** The saved plan ("My FIRE plan") — the dashed baseline everything is measured against. */
  planResult: LifeResult | null
  pending: boolean
  /** Any read (fan, insights, saved plan) last failed — what it feeds is cleared or stale. */
  error: boolean
  retry: () => void
  /** The key the CURRENT `bundle.result` was computed for — compare against the live key to
   *  know the fan on screen corresponds to the current levers (state like `pending` flips one
   *  render late, so it alone can't answer that). */
  settledKey: string | null
} {
  const [bundle, setBundle] = useState<SimBundle | null>(null)
  const [planResult, setPlanResult] = useState<LifeResult | null>(null)
  const [pending, setPending] = useState(false)
  // Which read last failed. Quiet by design: the screen keeps what it has but says it may be
  // stale, and a failed insights/saved-plan read clears ITS figures instead of leaving old ones up.
  const [failed, setFailed] = useState({ fan: false, insights: false, plan: false })
  const fail = (part: 'fan' | 'insights' | 'plan', value: boolean) =>
    setFailed((f) => (f[part] === value ? f : { ...f, [part]: value }))
  const [settledKey, setSettledKey] = useState<string | null>(null)
  const [retryTick, setRetryTick] = useState(0)
  const alive = useRef(true)
  const busy = useRef(false)
  const dirty = useRef<{ req: SimRequest; key: string } | null>(null)
  const latestFan = useRef(0)
  const planSeq = useRef(0)
  const runRef = useRef<((r: SimRequest, k: string) => void) | null>(null)
  // Latest params in a ref so the effects post the freshest values without re-subscribing.
  // Declared before them: effects run in order, so it is current when they read it.
  const latest = useRef({ req, key })
  useEffect(() => {
    latest.current = { req, key }
  })

  useEffect(() => {
    alive.current = true
    const run = (r: SimRequest, k: string) => {
      const id = ++latestFan.current
      busy.current = true
      setPending(true)
      const finish = () => {
        busy.current = false
        setPending(false)
        const next = dirty.current
        dirty.current = null
        if (next) run(next.req, next.key)
      }
      fetchOutlook(r.levers, { quick: !r.full, outcomes: r.chartMode === 'rbd' })
        .then(({ result, outcomes }) => {
          if (!alive.current) return
          setBundle((prev) => ({ ...(prev ?? NO_INSIGHTS), result, rbdData: outcomes }))
          setSettledKey(k)
          fail('fan', false)
          finish()
          // The slow reads arrive after the fan so they never hold it up; a newer request
          // supersedes them (the fan it triggers brings its own).
          if (r.full) {
            fetchInsights(r.levers)
              .then((insights) => {
                if (!alive.current || id !== latestFan.current) return
                setBundle((prev) => (prev ? { ...prev, ...insights } : prev))
                fail('insights', false)
              })
              .catch(() => {
                if (!alive.current || id !== latestFan.current) return
                setBundle((prev) => (prev ? { ...prev, ...NO_INSIGHTS } : prev))
                fail('insights', true)
              })
          }
        })
        .catch(() => {
          if (!alive.current) return
          fail('fan', true)
          finish()
        })
    }
    runRef.current = run
    return () => {
      alive.current = false
      runRef.current = null
    }
  }, [])

  // Fan: post on lever change — coalesce to the latest while a request is in flight.
  useEffect(() => {
    if (!enabled || !runRef.current) return
    if (busy.current) dirty.current = latest.current
    else runRef.current(latest.current.req, latest.current.key)
  }, [enabled, key, retryTick])

  // Saved plan: cheap + infrequent — re-read whenever the saved baseline or the book moves.
  useEffect(() => {
    if (!enabled) return
    const id = ++planSeq.current
    fetchOutlook({})
      .then(({ result }) => {
        if (!alive.current || id !== planSeq.current) return
        setPlanResult(result)
        fail('plan', false)
      })
      .catch(() => {
        if (!alive.current || id !== planSeq.current) return
        setPlanResult(null)
        fail('plan', true)
      })
  }, [enabled, planKey, retryTick])

  const retry = useCallback(() => setRetryTick((t) => t + 1), [])

  return {
    bundle,
    planResult,
    pending,
    error: failed.fan || failed.insights || failed.plan,
    retry,
    settledKey,
  }
}
