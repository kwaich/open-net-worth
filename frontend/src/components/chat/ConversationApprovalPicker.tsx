import { useEffect, useState } from 'react'
import { PermissionModeControl, type PermissionMode } from '../ui'
import { getPermissionMode, setPermissionMode } from '../../api/permissionMode'

/** How much this conversation asks before it acts — manual / auto / skip.
 *
 * Renders `ui/PermissionModeControl`, the SAME control the Automation editor uses: one
 * three-way choice should not look like two different controls depending on where you meet
 * it (owner, 2026-08-27).
 *
 * Lives in its own file because there are TWO composers — the floating dock
 * (`lucius/LuciusDock` via `ModelEffortCluster`) and the side panel (`ChatPanel`) — which
 * build their toolbars separately. Wiring only one is exactly the mistake that shipped it
 * invisible the first time.
 *
 * The mode is stored ON the conversation (RFC 2026-08-27-per-conversation-approval-mode), so
 * it cannot outlive it. A brand-new conversation starts in `auto`; `manual` — what every
 * EXISTING conversation keeps behaving as — is a deliberate no-op server-side.
 */
type Known = { mode: PermissionMode; editable: boolean }
// The UNKNOWN placeholder is the mode that ASKS — never `skip` — but it is not shown: a picker
// that painted "Manual" and flipped to the conversation's real mode a moment later was the
// last show-then-change left on opening a conversation. Read once per conversation and
// remembered, so a revisit paints the real mode on the first frame.
const UNKNOWN: Known = { mode: 'manual', editable: true }
const knownModes = new Map<string, Known>()

export function ConversationApprovalPicker({ sessionId }: { sessionId: string }) {
  const [state, setState] = useState(() => ({ sessionId, known: knownModes.get(sessionId) }))
  // A different conversation on the same mount reads ITS remembered mode, never the last one's.
  const known = state.sessionId === sessionId ? state.known : knownModes.get(sessionId)
  const { mode, editable } = known ?? UNKNOWN

  useEffect(() => {
    if (!sessionId) return
    let live = true
    getPermissionMode(sessionId)
      .then((read) => {
        if (!live) return
        const next = { mode: read.mode, editable: read.editable }
        knownModes.set(sessionId, next)
        setState({ sessionId, known: next })
      })
      .catch(() => {
        // A failed read shows `manual` — the mode that ASKS. Never `skip`.
        if (live) setState({ sessionId, known: UNKNOWN })
      })
    return () => {
      live = false
    }
  }, [sessionId])

  const setMode = (next: PermissionMode) => {
    const updated = { mode: next, editable }
    knownModes.set(sessionId, updated)
    setState({ sessionId, known: updated })
  }

  if (!sessionId) return null

  return (
    // `display: contents` — the slot adds no box, so hiding the unknown placeholder keeps its
    // space and nothing in the toolbar moves when the real mode arrives.
    <span className={'lx-approval-slot' + (known ? '' : ' lx-approval-slot--pending')}>
      <PermissionModeControl
        value={mode}
        subject="Approvals in this conversation"
        tone="bare"
        disabled={!editable}
        // An Automation run's mode belongs to the Automation. Showing it read-only is the
        // honest option: hiding it would leave no answer to "what governs this run?", and
        // leaving it editable wrote a value the gate never reads.
        disabledReason="Set by this Automation — change it on the Automation."
        onChange={(next) => {
          const previous = mode
          setMode(next)
          // Optimistic, and REVERTS on failure: leaving the new mode on screen after a
          // rejected write costs the user a belief about their own permissions that is not
          // true (the same reasoning as ChangePreview's grant error).
          setPermissionMode(sessionId, next).catch(() => setMode(previous))
        }}
      />
    </span>
  )
}
