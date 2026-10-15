// Lucius asking the app to show one of its guides (the show_guide tool). The chat stream hands
// the request here as the tool's result arrives; the shell's guide host shows it. A plain
// pub-sub, so the reducer stays free of React.
export type GuideRequest = 'attach' | 'lucius_tour'

const listeners = new Set<(g: GuideRequest) => void>()

export function requestGuide(guide: GuideRequest): void {
  listeners.forEach((l) => l(guide))
}

export function onGuideRequest(listener: (g: GuideRequest) => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** The guide a show_guide result names, or null for any other tool's result. */
export function guideFromResult(result: unknown): GuideRequest | null {
  if (typeof result !== 'string' || !result.includes('"ui_guide"')) return null
  try {
    const g = (JSON.parse(result) as { ui_guide?: unknown }).ui_guide
    return g === 'attach' || g === 'lucius_tour' ? g : null
  } catch {
    return null
  }
}
