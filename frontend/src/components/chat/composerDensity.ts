// How much of the composer's toolbar cluster (cost · model · effort) fits beside the fixed
// left icon group and Send, by the composer's rendered width. Measured 2026-09-15 on the
// docked panel: the left group is ~186px and Send 28px, so a 400px panel (the default)
// leaves ~119px for the whole cluster — and a long model id ("z-ai/glm-5.3-flash") wants
// ~140px on its own. One 340px cutoff read as "everything crammed, model chopped to
// 'z-ai/…'" right up to the line (owner report), so the cluster now sheds its least useful
// part first instead of holding all three until nothing fits.

/** Below this the session-cost figure goes (it is on every message's cost line anyway). */
export const COST_MIN_WIDTH = 480
/** Below this the effort's trailing label goes; the tier stays one tap in, via the panel. */
export const EFFORT_LABEL_MIN_WIDTH = 440
/** Below this the model picker goes too — widen the panel to change the model. */
export const MODEL_MIN_WIDTH = 360

export interface ComposerDensity {
  showCost: boolean
  showEffortLabel: boolean
  showModel: boolean
}

export function composerDensity(width: number): ComposerDensity {
  return {
    showCost: width >= COST_MIN_WIDTH,
    showEffortLabel: width >= EFFORT_LABEL_MIN_WIDTH,
    showModel: width >= MODEL_MIN_WIDTH,
  }
}
