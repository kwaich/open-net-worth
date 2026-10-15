// Render only the TAIL of a long transcript. Mounting every markdown bubble in one
// synchronous React commit froze the Lucius tab switch on mobile (154 messages ≈ 5–10s of
// blocked main thread on an iPhone — the tab wouldn't even visually switch). The window is
// a render decision, not a data one: nothing is dropped, "show earlier" reveals the rest.

// Small on purpose: expanding mounts `STEP` more markdown bubbles in ONE synchronous
// commit — 100 at a time recreated the very freeze the window exists to prevent (a
// message here averages ~25KB of digest markdown). `content-visibility: auto` on
// .msg-bubble makes off-screen ones cheap AFTER mount; the commit itself is what steps.
export const TRANSCRIPT_WINDOW_INITIAL = 24
export const TRANSCRIPT_WINDOW_STEP = 24

export function windowedTranscript<T>(
  messages: readonly T[],
  window: number,
): { visible: readonly T[]; hiddenCount: number } {
  const keep = Math.max(1, Math.floor(window))
  const hiddenCount = Math.max(0, messages.length - keep)
  if (hiddenCount === 0) return { visible: messages, hiddenCount: 0 }
  return { visible: messages.slice(hiddenCount), hiddenCount }
}
