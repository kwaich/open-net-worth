// CompactingActivity — the "compacting conversation" progress line, pinned above the
// composer in the exact slot TurnActivity occupies once the fold finishes.
//
// A sibling of TurnActivity, not a mode of it: TurnActivity's rotating word exists
// specifically to compensate for having NO real signal ("a liveness cue, not a status
// report"). Compacting is the opposite case — the backend streams a real percentage
// (backend/ai/assistant.py::_generate_fold_summary, RFC context-folding-v2) — so this
// component shows a real bar instead of inventing a word to rotate. The two share the
// same row-1 visual language (dot, shimmer, meta line) via the reused .lx-activity
// classes, so the pair reads as one continuous family; only the label is static here
// and a real Meter sits underneath.
//
// Mounts once per compacting phase (chatReducer sets `compacting` on compacting_start,
// clears it on compacting_done) — unlike TurnActivity it does NOT need a `key` remount
// trick, since compacting is a single bounded phase per turn, not something that
// continues across multiple steps within one longer-running turn.

import { useEffect, useRef, useState } from 'react'
import { Meter } from '../ui/Meter'
import { formatElapsed } from './TurnActivity'

export interface CompactingActivityProps {
  percent: number
  outputTokens?: number
  startedAt?: number
}

export function CompactingActivity({ percent, outputTokens, startedAt }: CompactingActivityProps) {
  const [elapsed, setElapsed] = useState(() =>
    startedAt ? Math.max(0, Date.now() - startedAt) : 0,
  )
  const localStartedAt = useRef(startedAt ?? Date.now())

  useEffect(() => {
    if (startedAt) {
      localStartedAt.current = startedAt
      setElapsed(Math.max(0, Date.now() - startedAt))
    }
  }, [startedAt])

  // Latest server-reported token count. Seeded immediately from the first render (no
  // reason to withhold real data that's already there), then committed into visible
  // state on the 1s clock tick thereafter — bounds the aria-live region to at most one
  // mutation/sec even though `outputTokens` can arrive far faster over SSE. The bar
  // itself (Meter, role="progressbar") has no such limit — its value is free to update
  // every frame, since a progressbar role doesn't queue an announcement the way
  // aria-live does.
  const latestTokens = useRef(outputTokens)
  const [tickedTokens, setTickedTokens] = useState(outputTokens)

  useEffect(() => {
    latestTokens.current = outputTokens
  }, [outputTokens])

  useEffect(() => {
    const tick = () => {
      setElapsed(Math.max(0, Date.now() - localStartedAt.current))
      setTickedTokens(latestTokens.current)
    }
    tick()
    const clock = setInterval(tick, 1000)
    return () => clearInterval(clock)
  }, [])

  return (
    <div className="lx-compacting">
      <div className="lx-activity" role="status" aria-live="polite">
        <span className="lx-activity-dot" aria-hidden="true" />
        <span className="lx-activity-word">Compacting conversation…</span>
        <span className="lx-activity-meta">
          ({formatElapsed(elapsed)}
          {typeof tickedTokens === 'number' && tickedTokens > 0
            ? ` • ↓ ${tickedTokens >= 1000 ? `${(tickedTokens / 1000).toFixed(1)}k` : tickedTokens} tokens`
            : ''}
          )
        </span>
      </div>
      <Meter
        variant="linear"
        size="sm"
        value={percent}
        color="var(--lucius)"
        ariaLabel="Compacting conversation progress"
        valueText={`${Math.round(percent)}%`}
        className="lx-compact-bar"
      />
    </div>
  )
}
