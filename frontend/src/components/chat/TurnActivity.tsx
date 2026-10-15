// TurnActivity — the "still working" line, pinned above the composer.
//
// Not inline in the transcript. A working indicator that lives in the message list
// scrolls away exactly when you want it (a long tool run pushes it off screen), and it
// competes with the answer for the reader's attention. Pinned above the composer it is
// always visible, always in the same place, and next to the Stop control that acts on it.
//
// The rotating word is a LIVENESS cue, not a status report: it deliberately does not claim
// to describe what the model is doing, because inventing plausible-sounding step names
// would be a claim the reader cannot check. The elapsed clock is the honest part.

import { useEffect, useRef, useState } from 'react'
import type { Message } from '../../types/chat'

const WORDS = [
  'Thinking',
  'Pondering',
  'Mulling',
  'Noodling',
  'Percolating',
  'Ruminating',
  'Cogitating',
  'Deliberating',
  'Scheming',
  'Puzzling',
  'Untangling',
  'Wrangling',
  'Crunching',
  'Divining',
  'Conjuring',
  'Assembling',
  'Tinkering',
  'Brewing',
  'Simmering',
  'Sautéing',
  'Whisking',
  'Marinating',
]

/** "1m 56s" / "8s" — minutes only once there are any. */
export function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000)
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`
}

/** How long a turn may show nothing new before the line says so plainly. */
export const QUIET_AFTER_MS = 10_000

/** A number that moves whenever the turn shows something new: text or reasoning arriving, a
 *  step starting or finishing. Only its CHANGE matters, never its value. */
export function turnProgress(messages: Message[]): number {
  const last = messages[messages.length - 1]
  if (!last) return 0
  const blocks = last.blocks ?? []
  const done = blocks.filter((b) => b.type === 'tool' && b.status === 'done').length
  return messages.length + last.text.length + (last.thinking?.length ?? 0) + blocks.length + done
}

// Remounted per step by the caller (`key={count of assistant messages}`), so the clock
// reads "time on THIS step" rather than accumulating across a turn that keeps continuing —
// a number that grows past a finished answer looks like a hang. Keyed on the ASSISTANT
// message count specifically, not total messages.length: a mid-turn queued message is
// optimistically appended to the array right away (useChat.sendMessage), which would bump
// messages.length and reset this clock for a turn that hasn't advanced at all — live
// incident 2026-08-10, "the timer resets whenever I send a queued message."
export interface TurnActivityProps {
  tokens?: number
  effort?: string
  startedAt?: number
  /** `turnProgress` of the transcript — when it stops changing the line turns to "Still
   *  working…", so a quiet spell reads as alive and not as a hang. */
  progress?: number
}

export function TurnActivity({ tokens, effort, startedAt, progress }: TurnActivityProps) {
  // Random start so two conversations working side by side do not tick in lockstep, which
  // reads as one shared spinner rather than two independent turns.
  const [i, setI] = useState(() => Math.floor(Math.random() * WORDS.length))
  const [elapsed, setElapsed] = useState(() =>
    startedAt ? Math.max(0, Date.now() - startedAt) : 0,
  )
  const localStartedAt = useRef(startedAt ?? Date.now())
  const lastProgress = useRef({ value: progress, at: Date.now() })
  const [quiet, setQuiet] = useState(false)

  useEffect(() => {
    if (progress !== lastProgress.current.value) {
      lastProgress.current = { value: progress, at: Date.now() }
      setQuiet(false)
    }
  }, [progress])

  useEffect(() => {
    if (startedAt) {
      localStartedAt.current = startedAt
      setElapsed(Math.max(0, Date.now() - startedAt))
    }
  }, [startedAt])

  const reduced =
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  useEffect(() => {
    // The clock ticks even under reduced-motion — it is information, not decoration. Only
    // the word rotation and the pulse are motion.
    const tick = () => {
      setElapsed(Math.max(0, Date.now() - localStartedAt.current))
      setQuiet(Date.now() - lastProgress.current.at >= QUIET_AFTER_MS)
    }
    tick()
    const clock = setInterval(tick, 1000)
    if (reduced) return () => clearInterval(clock)
    const words = setInterval(() => setI((n) => (n + 1) % WORDS.length), 6000)
    return () => {
      clearInterval(clock)
      clearInterval(words)
    }
  }, [reduced])

  return (
    <div className="lx-activity" role="status" aria-live="polite">
      <span className="lx-activity-dot" aria-hidden="true" />
      <span key={quiet ? 'quiet' : i} className="lx-activity-word">
        {quiet ? 'Still working' : WORDS[i]}…
      </span>
      <span className="lx-activity-meta">
        ({formatElapsed(elapsed)}
        {/* Real server-reported output tokens (turn_progress), not an estimate — so it is
            simply absent until the first model round reports, rather than showing a
            fabricated running guess. */}
        {typeof tokens === 'number' && tokens > 0
          ? ` • ↓ ${tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : tokens} tokens`
          : ''}
        {effort ? ` • thinking with ${effort} effort` : ''})
      </span>
    </div>
  )
}
