// ConversationStatusBadge.tsx — the per-row status glyph in the sidebar (ConversationMenu)
// and the full list (ConversationBrowser).
//
// .claude/rfcs/concurrent-conversations-ui-spec.md §2/§5.1. Three genuinely different
// SHAPES for the three "look at me" states — Bell (needs_you), AlertCircle (failed), and a
// dot with a different fill (working pulses, done is static) — satisfy "not colour alone"
// (acceptance E5) more robustly than three colours of the same dot would. `idle` renders
// nothing at all: absence of a marker IS the idle state, not a grey placeholder dot.
//
// This renders ONLY the leading glyph. The human-readable word ("Needs you" / "Working…" /
// "New reply" / "Failed") lives in the row's own trailing slot (`conv-menu-row-w` /
// `conv-browser-when`, swapped for the timestamp) — see `conversationStatusLabel` below —
// so the word is real, visible text in the row's own accessible name instead of being
// duplicated inside this glyph.

import { AlertCircle, Bell } from '../../ui/icons'
import type { ConvRowStatus } from '../../types/chat'

const LABELS: Record<Exclude<ConvRowStatus, 'idle'>, string> = {
  needs_you: 'Needs you',
  working: 'Working…',
  done: 'New reply',
  failed: 'Failed',
  run_failed: 'Failed',
}

const VARIANT_CLASS: Record<Exclude<ConvRowStatus, 'idle'>, string> = {
  needs_you: 'conv-row-status--needs-you',
  working: 'conv-row-status--working',
  done: 'conv-row-status--done',
  failed: 'conv-row-status--failed',
  run_failed: 'conv-row-status--failed',
}

/** The word a row's trailing slot shows in place of its timestamp — `null` for idle, so
 *  every caller can fall straight back to the timestamp with one `??`. */
export function conversationStatusLabel(status: ConvRowStatus): string | null {
  return status === 'idle' ? null : LABELS[status]
}

export function ConversationStatusBadge({ status }: { status: ConvRowStatus }) {
  if (status === 'idle') return null
  // Decorative: the row's trailing status word is real visible text and already carries
  // the meaning into the row button's accessible name — this glyph would only duplicate it.
  return (
    <span className={`conv-row-status ${VARIANT_CLASS[status]}`} aria-hidden="true">
      {status === 'needs_you' ? (
        <Bell size={11} strokeWidth={1.8} className="conv-row-status-icon" />
      ) : status === 'failed' || status === 'run_failed' ? (
        <AlertCircle size={11} strokeWidth={1.8} className="conv-row-status-icon" />
      ) : (
        <span className="conv-row-status-dot" />
      )}
    </span>
  )
}
