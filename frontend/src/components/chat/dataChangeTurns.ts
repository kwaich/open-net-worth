import type { Message } from '../../types/chat'

/** The user messages whose turn changed the book's data: any reply before the next user
 *  message carries a write receipt ("2 transactions added"). Saving a profile field leaves no
 *  receipt, so the name at the start of onboarding is not one — the Restore guide waits for
 *  the first real change instead of teaching "undo" on a name. */
export function dataChangeTurns(messages: readonly Message[]): Set<string> {
  const out = new Set<string>()
  let owner: string | null = null
  for (const m of messages) {
    if (m.role === 'user') owner = m.id
    else if (owner && (m.blocks || []).some((b) => b.type === 'receipt')) out.add(owner)
  }
  return out
}
