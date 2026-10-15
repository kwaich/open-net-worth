import { useEffect, useState, type CSSProperties } from 'react'

// Keep in step with `skeleton-breathe` / `skeleton-enter` in index.css.
const BREATHE_MS = 2400
const ENTER_DELAY_MS = 160
// A skeleton mounted this soon after another one left is the SAME wait, handed over.
const HANDOFF_MS = 400

let skeletonsOnScreen = 0
let lastSkeletonLeftAt = -Infinity
let waitStartedAt = 0

const now = () => (typeof performance === 'undefined' ? Date.now() : performance.now())

/**
 * A load often passes through several skeletons (route chunk → tab → sub-pane). Each is a new
 * mount, so a naive fade-in restarts at every handoff — a half-faded skeleton snaps to a fresh
 * one, read as a double flash. Returns the container's animation delay: a skeleton that
 * continues one already on screen resumes the SAME fade where it stands, so the whole wait
 * fades in exactly once however many components take turns drawing it.
 */
export function useSkeletonHandoff(): CSSProperties {
  const [delay] = useState(() => {
    const t = now()
    if (skeletonsOnScreen === 0 && t - lastSkeletonLeftAt >= HANDOFF_MS) waitStartedAt = t
    return Math.round(ENTER_DELAY_MS - (t - waitStartedAt))
  })
  useEffect(() => {
    skeletonsOnScreen += 1
    return () => {
      skeletonsOnScreen -= 1
      lastSkeletonLeftAt = now()
    }
  }, [])
  return { animationDelay: `${delay}ms` }
}

/** Negative animation delay that joins the page-wide breathe cycle already in progress. */
export function skeletonPhaseMs(): number {
  return -Math.round(now() % BREATHE_MS)
}
