import type { StoryArc } from './storyMeta'

// Who a story is about — the first cut Intel's tabs are built on (intelTabs.partitionByTab):
// `holdings` stories go on My holdings; `watching` ones go to the list tabs holding the ticker;
// `macro` ones (and anything no list claims) go to Topics & macro.
export type IntelScope = 'holdings' | 'watching' | 'macro'

/**
 * First match wins:
 *  1. a ticker you hold          → holdings
 *  2. a ticker you watch         → watching
 *  3. a portfolio-level risk / price alert with no known ticker → holdings
 *  4. macro or topic             → macro
 *  5. any other ticker (a market discovery you neither hold nor watch) → macro
 *  6. anything else              → holdings
 * `held` and `watched` must be upper-cased tickers.
 */
export function storyScope(s: StoryArc, held: Set<string>, watched: Set<string>): IntelScope {
  const ticker = (s.ticker ?? '').trim().toUpperCase()
  if (ticker && held.has(ticker)) return 'holdings'
  if (ticker && watched.has(ticker)) return 'watching'
  if (s.type === 'risk' || s.type === 'price_alert') return 'holdings'
  if (s.type === 'macro' || s.type === 'topic') return 'macro'
  if (ticker) return 'macro'
  return 'holdings'
}
