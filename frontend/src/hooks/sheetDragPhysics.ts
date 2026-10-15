// sheetDragPhysics — pure math for useSheetDragToDismiss. No DOM, no React: kept separate so
// the drag/dismiss thresholds, the rubber-band curve, and the velocity window are each testable
// as plain functions instead of only reachable through a simulated pointer sequence.

/** How far, as a ratio of the sheet's own height, a downward drag pulls before it counts as
 * "release to dismiss". A drag only ever starts from a resting sheet (content scrolls natively
 * and never hands off mid-gesture — see useSheetDragToDismiss), so every pixel of offset is the
 * user's own deliberate pull; no headroom is needed for velocity inherited from a scroll. */
export const DISMISS_DISTANCE_RATIO = 0.35
/** Downward release speed (px/ms) that dismisses regardless of distance — a flick. */
export const DISMISS_VELOCITY_PX_MS = 0.5
/** Cap on the upward rubber-band pull — these are fixed-height sheets, not multi-detent, so an
 * upward drag past rest never tracks 1:1. */
export const RUBBER_BAND_MAX_PX = 18
/** Scrim opacity never reaches 0 mid-drag — that would hide the backdrop before the sheet is
 * actually gone. Floor is relative to whatever the scrim's rest opacity is. */
export const SCRIM_OPACITY_FLOOR_RATIO = 0.15
/** Velocity is measured from samples within this trailing window, not the whole gesture — a
 * slow drag that ends in a fast flick should read as fast, not averaged down. */
export const VELOCITY_WINDOW_MS = 80

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/** The sheet's live transform offset for a given pointer delta: 1:1 while pulling down
 * (delta >= 0), damped toward `max` while pulling up past rest (delta < 0). */
export function dragOffset(delta: number, max = RUBBER_BAND_MAX_PX): number {
  if (delta >= 0) return delta
  return -(max * (1 - Math.exp(-Math.abs(delta) / max)))
}

/** 0 at rest, 1 once the sheet has been pulled its own full height down. Pulling up (delta < 0)
 * never contributes negative progress. */
export function dragProgress(deltaY: number, sheetHeight: number): number {
  if (sheetHeight <= 0) return 0
  return clamp(deltaY / sheetHeight, 0, 1)
}

export function scrimOpacity(
  progress: number,
  restOpacity = 1,
  floorRatio = SCRIM_OPACITY_FLOOR_RATIO,
): number {
  return restOpacity * (1 - clamp(progress, 0, 1) * (1 - floorRatio))
}

export interface PointerSample {
  t: number
  y: number
}

/** px/ms, positive = moving down. Uses the oldest sample still inside the trailing window
 * rather than the whole buffer, so a drag that starts slow and ends in a flick reads as fast. */
export function computeVelocity(samples: PointerSample[], windowMs = VELOCITY_WINDOW_MS): number {
  if (samples.length < 2) return 0
  // Length already checked above — both indices are in range.
  const latest = samples[samples.length - 1]!
  const cutoff = latest.t - windowMs
  // Searched separately from `latest`: if no PRIOR sample falls inside the window (samples
  // arrived sparser than `windowMs`, e.g. a throttled drag), fall back to the oldest prior
  // sample rather than comparing `latest` against itself, which would silently read as 0.
  const priorSamples = samples.slice(0, -1)
  const earliest = priorSamples.find((s) => s.t >= cutoff) ?? priorSamples[0]!
  const dt = latest.t - earliest.t
  return dt > 0 ? (latest.y - earliest.y) / dt : 0
}

export interface CommitDecisionInput {
  offsetY: number
  sheetHeight: number
  velocity: number
  distanceRatio?: number
  velocityThreshold?: number
}

/** Release commits to dismiss when EITHER the drag crossed the distance ratio of the sheet's
 * own height, OR the release velocity alone reads as a flick — an upward/zero drag never
 * commits, regardless of velocity noise. */
export function shouldCommitDismiss({
  offsetY,
  sheetHeight,
  velocity,
  distanceRatio = DISMISS_DISTANCE_RATIO,
  velocityThreshold = DISMISS_VELOCITY_PX_MS,
}: CommitDecisionInput): boolean {
  if (offsetY <= 0 || sheetHeight <= 0) return false
  return offsetY / sheetHeight > distanceRatio || velocity > velocityThreshold
}
