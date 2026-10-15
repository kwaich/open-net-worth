import { STATUS_META } from './storyMeta'
import type { StoryArc } from './storyMeta'

const CURRENT_DAYS = 7
const CURRENT_MS = CURRENT_DAYS * 24 * 60 * 60 * 1000

export type QueueStatus = StoryArc['status'] | 'needs_recheck'

export function isFreshSignal(signal: Pick<StoryArc, 'updated_at'>, now = Date.now()): boolean {
  const updated = Date.parse(signal.updated_at)
  return Number.isFinite(updated) && updated <= now + 5 * 60_000 && now - updated <= CURRENT_MS
}

export function classifySignal(
  signal: Pick<StoryArc, 'status' | 'action_items' | 'updated_at'>,
  now = Date.now(),
): QueueStatus {
  if (signal.status !== 'action_needed') return signal.status
  // Only an explicit "No action needed" says there is no decision. A deterministic watch story
  // (services/watch_signals.py) carries no action items by design — a breakdown or entering your
  // buy zone is still a decision to make, so an EMPTY list keeps its status.
  const items = (signal.action_items ?? []).map((item) => item.trim()).filter(Boolean)
  const hasDecision =
    items.length === 0 || items.some((action) => !/^no action (needed|required)/i.test(action))
  if (!hasDecision) return 'monitoring'
  return isFreshSignal(signal, now) ? 'action_needed' : 'needs_recheck'
}

/** The queue's order and labels: a stale or no-decision "action needed" signal reads as
 *  "Needs recheck" or "Monitoring" everywhere a status is shown or counted. */
export const QUEUE_ORDER: QueueStatus[] = ['action_needed', 'needs_recheck', 'active', 'monitoring']

export const QUEUE_META: Record<QueueStatus, { label: string; color: string; rank: number }> = {
  action_needed: { ...STATUS_META.action_needed, rank: 0 },
  needs_recheck: { label: 'Needs recheck', color: 'var(--warn)', rank: 1 },
  active: { ...STATUS_META.active, rank: 2 },
  monitoring: { ...STATUS_META.monitoring, rank: 3 },
}
