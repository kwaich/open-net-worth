// The runs between a sparkline and its comparison line — pure, so it is unit-tested alone.

interface GapPoint {
  /** Position in index units — fractional where the lines cross. */
  x: number
  v: number
  c: number
}
export interface GapRun {
  ahead: boolean
  pts: GapPoint[]
}

/** Split two equal-length series into runs where `values` stays on one side of `compare`, each
 *  run ending (and the next starting) at the interpolated crossing. */
export function gapRuns(values: number[], compare: number[]): GapRun[] {
  const n = Math.min(values.length, compare.length)
  // A point where the lines touch takes the side of the next point that doesn't (else the last
  // one) — the lines always start together, and that first touch belongs to no side.
  const side: (boolean | null)[] = Array.from({ length: n }, (_, i) =>
    values[i]! === compare[i]! ? null : values[i]! > compare[i]!,
  )
  for (let i = n - 2; i >= 0; i--) side[i] ??= side[i + 1]!
  for (let i = 1; i < n; i++) side[i] ??= side[i - 1]!
  const runs: GapRun[] = []
  let cur: GapRun | null = null
  for (let i = 0; i < n; i++) {
    const v = values[i]!
    const c = compare[i]!
    const ahead = side[i] ?? true
    if (cur && ahead !== cur.ahead) {
      const pv = values[i - 1]!
      const pd = pv - compare[i - 1]!
      const t = pd / (pd - (v - c))
      const at = pv + (v - pv) * t
      const cross = { x: i - 1 + t, v: at, c: at }
      cur.pts.push(cross)
      runs.push(cur)
      cur = { ahead, pts: [cross] }
    }
    if (!cur) cur = { ahead, pts: [] }
    cur.pts.push({ x: i, v, c })
  }
  if (cur && cur.pts.length > 1) runs.push(cur)
  return runs
}
