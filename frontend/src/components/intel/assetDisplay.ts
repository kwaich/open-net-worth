import type { AssetInfo } from '../../types/assetDetail'

/**
 * User-facing ticker for the header subline and Key Facts row.
 *
 * For crypto the stored `ticker` is the CoinGecko slug (e.g. "shiba-inu", "pepe") —
 * an internal id, not what a user recognises. Show the real symbol ("SHIB", "PEPE")
 * instead. Non-crypto keeps the exchange-qualified ticker (e.g. "KSTL.DE").
 *
 * Display-only: the watchlist follow round-trip still keys off `asset.ticker`
 * (the price_source_id), so this never touches that path.
 */
export function displayTicker(asset: Pick<AssetInfo, 'asset_class' | 'symbol' | 'ticker'>): string {
  const isCrypto = (asset.asset_class || '').toLowerCase() === 'crypto'
  if (isCrypto && asset.symbol) return asset.symbol
  return asset.ticker || asset.symbol || ''
}

// NOTE: the security-type suffix strip ("…Class A Common Stock" → "…Corp.") now lives
// server-side as the single source of truth — backend/core/instrument_display.py. The
// API serves already-cleaned names, so views render `asset.name` / `row.label` directly.
