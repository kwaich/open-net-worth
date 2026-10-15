/**
 * Pure formatting helpers for the AI cost panel.
 * No imports from React or the rest of the app — safe to test in node.
 */

/** Format a USD cost value with 4 decimal places.
 *  Sub-cent AI charges need more precision than the standard 2dp.
 *  e.g. fUsd(0.000234) → "$0.0002", fUsd(1.2345) → "$1.2345"
 */
export function fUsd(n: number): string {
  if (!isFinite(n)) return '$0.0000'
  if (n === 0) return '$0.0000'
  return '$' + n.toFixed(4)
}

/** Compact token count: 1,234 → "1.2K", 1,234,567 → "1.2M", below 1000 → exact. */
export function fTokens(n: number): string {
  if (!isFinite(n) || n < 0) return '0'
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M'
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
  return String(Math.round(n))
}

/** Human-readable label for a cost source string.
 *  "chat" → "Chat", "scheduled" → "Scheduled", "agent:<id>" → "Agent: <id>",
 *  "memory" → "Memory", anything else → the raw value (capitalised). */
export function labelSource(src: string): string {
  if (!src) return 'Unknown'
  if (src === 'chat') return 'Chat'
  if (src === 'scheduled') return 'Scheduled'
  if (src === 'memory') return 'Memory'
  if (src === 'headless') return 'Headless'
  if (src.startsWith('agent:')) return 'Agent: ' + src.slice(6)
  // Capitalise the first letter, replace underscores/hyphens with spaces.
  return src.charAt(0).toUpperCase() + src.slice(1).replace(/[_-]/g, ' ')
}

/** Totals shape returned by the cost API for each bucket. */
export interface CostTotals {
  turns: number
  input_tokens: number
  cache_creation_tokens: number
  cache_read_tokens: number
  output_tokens: number
  cost_usd: number
}

/** Sort a {key → CostTotals} map by cost_usd descending and return as an array. */
export function sortByCost(
  map: Record<string, CostTotals>,
): Array<{ key: string; totals: CostTotals }> {
  return Object.entries(map)
    .map(([key, totals]) => ({ key, totals }))
    .sort((a, b) => b.totals.cost_usd - a.totals.cost_usd)
}

/** Total billable tokens from a CostTotals entry (input + cache_creation + output;
 *  cache_read excluded as it is charged at a much lower rate). */
export function totalTokens(t: CostTotals): number {
  return t.input_tokens + t.cache_creation_tokens + t.output_tokens
}

/**
 * Sum the cost of all completed assistant turns in a conversation.
 * Only messages with a `usage.cost_usd` contribute (user messages and
 * in-progress streaming turns have no usage yet).
 */
export function sumSessionCost(messages: ReadonlyArray<{ usage?: { cost_usd: number } }>): number {
  return messages.reduce((acc, m) => acc + (m.usage?.cost_usd ?? 0), 0)
}
