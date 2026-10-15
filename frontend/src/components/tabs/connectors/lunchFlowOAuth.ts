// The popup + poll controller behind the Lunch Flow "Connect bank" button — this connector's
// OWN equivalent of `oauthSignIn.ts` (the MCP OAuth flow's popup+poll controller), reusing
// that SHAPE (not its code — Lunch Flow's flow carries no DCR/PKCE machinery to reuse) because
// it is the exact same dance: open a window, hand it the authorize URL once `begin()`
// answers, then learn how it finished.
//
// TWO independent signals, same reasoning as the MCP OAuth controller: a popup can be blocked
// and the operator can finish Lunch Flow's hosted sign-in on an entirely different device —
//   * a `postMessage` from the callback page (`backend/api/lunch_flow_api.py`'s
//     `_CALLBACK_HTML`) — the FAST path, ignored if it never arrives;
//   * a poll of `GET /api/lunch-flow/oauth/{flow_id}` every `POLL_MS` — the AUTHORITATIVE
//     path, the one thing this controller actually trusts to finish.
// The callback's message is `{source: "onw-lunch-flow-oauth"}` at `window.location.origin`
// (this app's own origin — never a caller-supplied one); receiving it just triggers an
// immediate poll tick rather than being treated as the answer itself.

import { beginLunchFlowOAuth, pollLunchFlowOAuth } from './lunchFlowApi'

const POLL_MS = 1500
const POPUP_NAME = 'onw-lunch-flow-oauth'
const POPUP_FEATURES = 'width=520,height=680'

export interface LunchFlowOAuthOutcome {
  ok: boolean
  error: string | null
}

/** Open a blank popup SYNCHRONOUSLY, before any `await` — `beginLunchFlowOAuth`'s fetch is
 *  async, and a `window.open` call made only after it resolves falls outside the "direct
 *  response to a click" window most browsers require, so it would be blocked far more often.
 *  Navigated to the real `authorize_url` once `begin()` answers. */
function openBlankPopup(): Window | null {
  try {
    return window.open('about:blank', POPUP_NAME, POPUP_FEATURES)
  } catch {
    return null
  }
}

export interface LunchFlowOAuthHandle {
  /** Stop polling and close the popup. Sends no request — the staged flow is simply left to
   *  expire on its own 10-minute TTL (`lunch_flow_pending.TTL_SECONDS`). */
  cancel: () => void
}

/**
 * Start a bank-link sign-in: open the popup, call `begin()`, then race a poll against a
 * `postMessage` until the flow resolves. `onOutcome` fires exactly once — on success, on any
 * `begin()` failure, on a poll `status: 'error'`, on the popup being blocked, or on the popup
 * being closed before the flow finished. Never fires after `cancel()`.
 */
export function startLunchFlowOAuth(
  onOutcome: (outcome: LunchFlowOAuthOutcome) => void,
): LunchFlowOAuthHandle {
  let popup = openBlankPopup()
  let cancelled = false
  let pollTimer: ReturnType<typeof setInterval> | null = null
  let messageListener: ((e: MessageEvent) => void) | null = null
  let polling = false

  function stop() {
    if (pollTimer !== null) clearInterval(pollTimer)
    pollTimer = null
    if (messageListener) window.removeEventListener('message', messageListener)
    messageListener = null
  }

  function finish(outcome: LunchFlowOAuthOutcome) {
    if (cancelled) return
    cancelled = true // finish() is terminal — guards a straggling tick from firing twice
    stop()
    popup?.close()
    onOutcome(outcome)
  }

  function fail(error: string) {
    finish({ ok: false, error })
  }

  async function tick(flowId: string) {
    if (cancelled || polling) return
    if (popup && popup.closed) {
      fail('The sign-in window was closed before finishing.')
      return
    }
    polling = true
    const poll = await pollLunchFlowOAuth(flowId)
    polling = false
    if (cancelled) return
    if (poll.status === 'pending') return
    finish({ ok: poll.status === 'ok', error: poll.status === 'error' ? poll.error : null })
  }

  void (async () => {
    const result = await beginLunchFlowOAuth()
    if (cancelled) return
    if (!result.ok) {
      finish({ ok: false, error: result.error ?? 'Could not start the sign-in.' })
      return
    }
    const flowId = result.flow_id
    const authorizeUrl = result.authorize_url
    if (!flowId || !authorizeUrl) {
      fail('The sign-in could not be started.')
      return
    }
    if (popup) popup.location.href = authorizeUrl
    else popup = window.open(authorizeUrl, POPUP_NAME, POPUP_FEATURES)
    if (!popup) {
      fail('Your browser blocked the sign-in window. Allow pop-ups for this site and try again.')
      return
    }

    messageListener = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return
      const data = e.data as { source?: unknown } | null
      if (!data || data.source !== 'onw-lunch-flow-oauth') return
      void tick(flowId) // the fast path — poll immediately rather than waiting out the interval
    }
    window.addEventListener('message', messageListener)
    pollTimer = setInterval(() => void tick(flowId), POLL_MS)
    void tick(flowId) // also fire once immediately, in case the flow already finished
  })()

  return {
    cancel: () => {
      cancelled = true
      stop()
      popup?.close()
    },
  }
}
