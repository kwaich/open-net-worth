// Whether the next prefill handed to Lucius should be SENT rather than left in the composer.
// A "Set up with Lucius" press is the user asking him to start, so it sends; every other
// prefill (a suggestion, "Ask Lucius about…") stays a draft to edit. One-shot: taking it clears it.
let sendNext = false

export function sendNextPrefill(): void {
  sendNext = true
}

export function takeSendIntent(): boolean {
  const send = sendNext
  sendNext = false
  return send
}
