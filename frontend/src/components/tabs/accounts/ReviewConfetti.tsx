import { useEffect, useRef } from 'react'

const COLORS = ['var(--good)', 'var(--primary)', 'var(--warn)', 'var(--overfunded)', 'var(--bad)']
const PIECES = 90
/** Where the pieces start, as a fraction of the box height — `.acct-review-confetti i { top }`. */
const ORIGIN_Y = 0.42
const FRAMES = 48

/** Launch speed (as a multiple of the terminal fall speed) that peaks a piece `rise` above its
 *  start under linear drag: the apex is (vt/k)·(u − ln(1+u)), solved for u by bisection. */
function launchFor(rise: number, terminal: number, drag: number) {
  const target = (rise * drag) / terminal
  let low = 0
  let high = 40
  for (let i = 0; i < 40; i++) {
    const mid = (low + high) / 2
    if (mid - Math.log1p(mid) < target) low = mid
    else high = mid
  }
  return low * terminal
}

/** One piece's path under linear air drag and gravity, closed form. Each piece aims at its OWN
 *  apex — anywhere up to the top edge, anywhere across the width — so the burst fills the box
 *  instead of drawing a ring, and it flows into the fall with no stop in between. The path runs
 *  until the piece leaves the bottom edge. */
function flight(width: number, height: number) {
  const drag = 2.6 + Math.random() * 1.6
  const terminal = height * (0.75 + Math.random() * 0.35)
  const rise = height * ORIGIN_Y * (0.15 + Math.random() * 0.9)
  const vy = -launchFor(rise, terminal, drag)
  const vx = (Math.random() - 0.5) * width * 0.95 * drag
  const sway = 6 + Math.random() * 10
  const wobble = 5 + Math.random() * 5
  const phase = Math.random() * Math.PI * 2
  const spin = (Math.random() < 0.5 ? -1 : 1) * (360 + Math.random() * 720)
  const at = (t: number) => {
    const decay = (1 - Math.exp(-drag * t)) / drag
    return {
      x: vx * decay + sway * Math.sin(wobble * t + phase) * Math.min(1, t * 2),
      y: (vy - terminal) * decay + terminal * t,
    }
  }
  const exit = height * (1 - ORIGIN_Y) + 20
  let seconds = 0.5
  while (at(seconds).y < exit && seconds < 6) seconds += 0.05
  const frames: Keyframe[] = []
  for (let step = 0; step <= FRAMES; step++) {
    const progress = step / FRAMES
    const { x, y } = at(progress * seconds)
    const scale = Math.min(1, 0.4 + progress * 8)
    frames.push({
      transform: `translate3d(${x.toFixed(1)}px, ${y.toFixed(1)}px, 0) rotate(${(spin * progress).toFixed(0)}deg) scale(${scale.toFixed(2)})`,
      opacity: 1,
    })
  }
  return { frames, duration: seconds * 1000, delay: Math.random() * 80 }
}

/** A one-shot burst over the "all caught up" state — no library. Decorative: aria-hidden,
 *  pointer-events:none, and not rendered at all under reduced motion (CSS). */
export function ReviewConfetti() {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const box = ref.current
    if (!box) return
    const { clientWidth: width, clientHeight: height } = box
    const animations = Array.from(box.children).flatMap((piece) => {
      if (typeof piece.animate !== 'function') return []
      const { frames, duration, delay } = flight(width, height)
      return [piece.animate(frames, { duration, delay, easing: 'linear', fill: 'both' })]
    })
    return () => animations.forEach((animation) => animation.cancel())
  }, [])

  return (
    <div
      ref={ref}
      className="acct-review-confetti"
      aria-hidden="true"
      data-testid="review-confetti"
    >
      {Array.from({ length: PIECES }, (_, index) => (
        <i
          key={index}
          className={index % 3 === 0 ? 'is-round' : undefined}
          style={{ '--color': COLORS[index % COLORS.length] } as React.CSSProperties}
        />
      ))}
    </div>
  )
}
