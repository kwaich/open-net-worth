/** Pure decision for ChatPanel's `isHomeEmpty` — whether homeMode should show the empty
 *  landing greeting instead of the transcript.
 *
 *  Regression (owner report, phone, 2026-09-06): while Lucius streamed, the panel rendered
 *  normally. The instant the turn ended, it painted the empty-conversation greeting over a
 *  conversation with six messages already in memory. The server's conversation listing (a
 *  stale copy that day) reported that session with `message_count: 0`, and the old inline
 *  expression trusted the listing alone — it never looked at the hydrated slice's own
 *  message count. A hydrated slice that actually HAS messages is ground truth and wins over
 *  any listing, stale or not. */
export function computeHomeEmpty(input: {
  homeMode: boolean
  onboarding: boolean
  streaming: boolean
  /** A send whose POST has not been answered yet: already busy, though not yet streaming.
   *  The onboarding turn clears `onboarding` in the same step it sends, so without this the
   *  empty landing (greeting, centred composer, start chips) painted over a running turn
   *  until the reply began (owner report 2026-10-11). */
  sending?: boolean
  hydrated: boolean
  sliceMessageCount: number
  listedMessageCount: number | undefined
}): boolean {
  if (input.hydrated && input.sliceMessageCount > 0) return false
  return (
    input.homeMode &&
    !input.onboarding &&
    !input.streaming &&
    !input.sending &&
    (input.listedMessageCount === undefined ? true : input.listedMessageCount === 0)
  )
}
