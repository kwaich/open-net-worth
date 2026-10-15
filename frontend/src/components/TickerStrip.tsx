// The topbar price ticker. Replaces the "As of … · Prices: live" stamp when enabled, keeping
// the same information at the head of the strip so nothing is lost.
//
// Deliberately visible under "Hide amounts" (owner, 2026-08-24): a share price and a day % are
// public market data, and the strip never shows position size or value, so nothing personal is
// on screen.

import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { fetchJson } from '../hooks/serverState'
import { useMarketTicker } from '../hooks/useMarketTicker'
import { useQueryCache } from '../state/queryCache'
import { getTickerMarketAlways, subscribeTicker } from '../tickerPref'
import { Sunrise, Sunset } from '../ui/icons'
import type { ComputedRow } from '../types/portfolio'

export interface TickerItem {
  key: string
  label: string
  price: number
  /** The instrument's OWN quote currency — gold in USD, a SGD-listed holding in SGD. NOT the
   *  app's base currency: these are native quotes and converting them would be a lie. */
  unit: string
  changePct: number | null
  kind: 'holding' | 'commodity' | 'coin' | 'fx' | 'equity'
  /** Symbol to open on tap. Undefined = not navigable (see the FX note in TickerStrip). */
  navSymbol?: string
  /** Set when price/changePct above are a pre/post-market figure, not the regular-session
   *  one — a compact strip has no room for a separate line, so the row substitutes the
   *  extended quote directly and this flags it with a distinct icon (owner ask 2026-08-31). */
  session?: 'pre' | 'post'
}

/** Holdings → ticker items. Market positions only (an account row has no market price), one
 *  item per symbol, ordered by portfolio weight DESCENDING.
 *
 *  Weight order, not biggest-mover order: a mover-sorted strip reshuffles itself every poll,
 *  so the same instrument jumps position while you are reading it. Weight only changes when
 *  holdings do. */
export function holdingItems(rows: ComputedRow[]): TickerItem[] {
  const seen = new Set<string>()
  return rows
    .filter((r) => !r.is_account_row && r.symbol && r._disp_unit_price > 0)
    .slice()
    .sort((a, b) => (b._base ?? 0) - (a._base ?? 0))
    .filter((r) => {
      const sym = r.symbol as string
      if (seen.has(sym)) return false
      seen.add(sym)
      return true
    })
    .map((r) => ({
      key: `h:${r.symbol}`,
      label: r.symbol as string,
      // A pre/post-market figure substitutes for the regular one directly (no room for a
      // second line here) — see TickerItem.session.
      price: r.extended_price ?? r._disp_unit_price,
      unit: r._disp_ccy,
      changePct: r.extended_change_pct ?? r._daily_plpct,
      kind: 'holding' as const,
      navSymbol: r.symbol as string,
      session: r.extended_session ?? undefined,
    }))
}

function fmtPrice(v: number): string {
  // Scale precision to magnitude rather than fixing it. Below a cent, FIXED decimals are not
  // enough: VND/SGD is ~0.00005, which renders as a flat "0.0000" at 4dp — a real rate shown
  // as zero. Significant digits keep small pairs readable without giving gold four decimals.
  if (v > 0 && v < 0.01) return v.toPrecision(3)
  const digits = v >= 1000 ? 0 : v >= 1 ? 2 : 4
  return v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function Item({ item, onOpen }: { item: TickerItem; onOpen: (sym: string) => void }) {
  const pct = item.changePct
  const dir = pct == null ? 'flat' : pct > 0 ? 'up' : pct < 0 ? 'down' : 'flat'
  // An FX pair's label already names both currencies ("USD/SGD"), so repeating the unit is
  // noise. Everything else needs it: "Gold 4,692" is ambiguous, "Gold 4,692 USD" is not.
  const showUnit = item.kind !== 'fx'
  const body = (
    <>
      <span className="ticker-item-sym">{item.label}</span>
      {item.session && (
        <span
          className="ticker-item-session"
          title={item.session === 'pre' ? 'Pre-market' : 'After hours'}
        >
          {item.session === 'pre' ? <Sunrise size={11} /> : <Sunset size={11} />}
        </span>
      )}
      <span className="ticker-item-px">{fmtPrice(item.price)}</span>
      {showUnit && <span className="ticker-item-ccy">{item.unit}</span>}
      {pct != null && (
        <span className={`ticker-item-chg ticker-item-chg--${dir}`}>
          {/* No glyph when flat. An arrow says "it moved, this way"; a dot in that slot said
              nothing and just read as punctuation stuck to the number (owner, 2026-08-24). */}
          {dir === 'up' ? '▲' : dir === 'down' ? '▼' : null}
          {Math.abs(pct).toFixed(2)}%
        </span>
      )}
    </>
  )
  if (!item.navSymbol) return <span className="ticker-item">{body}</span>
  const sym = item.navSymbol
  return (
    <button
      type="button"
      className="ticker-item ticker-item--tap"
      onClick={() => onOpen(sym)}
      title={`Open ${item.label}`}
    >
      {body}
    </button>
  )
}

export interface TickerStripProps {
  rows: ComputedRow[]
  priceStatus: string
  /** SHORT form — just the clock time. The head shares a 52px row with the whole strip, so a
   *  full "As of August 24, 2026 at 04:23 PM · Prices: live" left almost no room for prices
   *  (owner, 2026-08-24). The long form rides along as the title/aria-label instead. */
  asOf: string
  asOfLong: string
}

/** Does the book follow any ticker of its own? User-added watchlist names count; a name the
 *  app watches only because it is held does not need asking (a held name already earns the
 *  market half). Only read while it can still change the answer. */
function useFollowsATicker(enabled: boolean): boolean {
  const query = useQueryCache<{ items?: Array<{ type?: string }> }>(
    '/api/watchlist',
    () => fetchJson('/api/watchlist'),
    { enabled },
  )
  return Boolean(query.data?.items?.some((i) => i.type === 'ticker'))
}

export function TickerStrip({ rows, priceStatus, asOf, asOfLong }: TickerStripProps) {
  const held = useMemo(() => holdingItems(rows), [rows])
  const [always, setAlways] = useState(getTickerMarketAlways)
  useEffect(() => subscribeTicker(() => setAlways(getTickerMarketAlways())), [])
  // The market half (gold, coins, FX, mega-caps) is context for someone who invests. A book of
  // cash and cards would otherwise watch NVDA and Bitcoin scroll past with no tie to its own
  // money, so it appears only with a market position or a watchlist, or when switched on in
  // Settings. When hidden it is not even polled.
  const followsATicker = useFollowsATicker(held.length === 0 && !always)
  const showMarket = always || held.length > 0 || followsATicker
  const market = useMarketTicker(showMarket)
  // useNavigate, not useAssetNav(): the topbar renders OUTSIDE <AssetNavProvider>, so the
  // hook would throw here. This is the same destination openAsset() picks for a ticker.
  const navigate = useNavigate()
  const openSymbol = (symbol: string) =>
    navigate({
      to: '/quote/$symbol',
      params: { symbol },
      search: { from: undefined, label: undefined },
    })
  const items = useMemo(() => {
    const mkt: TickerItem[] = market.map((m) => ({
      key: `m:${m.symbol}`,
      label: m.label,
      price: m.price,
      unit: m.unit,
      changePct: m.change_pct,
      kind: m.kind,
      // FX is deliberately NOT navigable. /quote/$symbol is find-or-CREATE, so opening
      // "USDSGD=X" would mint a junk instrument in the user's own instrument list for
      // something that is a rate, not an asset. Commodities and coins are real instruments.
      // FX alone is non-navigable (see the comment above); equities and coins are both
      // real instruments and open the same way.
      navSymbol: m.kind === 'fx' ? undefined : m.symbol,
      session: m.session,
    }))
    // Holdings lead — they are the reason the owner looks at the strip; commodities, coins and
    // FX follow as context.
    return [...held, ...mkt]
  }, [held, market])

  // NO swap to a different element while empty. Rendering the stamp until data arrived meant
  // every reload visibly flipped stamp → ticker, because holdings AND the market fetch both
  // resolve after first paint — and the two elements are different heights, so the whole bar
  // jumped (owner, 2026-08-24: "it shouldn't show that before getting the results ... the
  // status bar height should be the same still"). The strip's own chrome renders immediately
  // and stays; only the scrolling viewport waits for data. Keeping .ticker mounted also keeps
  // `.topbar:has(.ticker)`'s 52px pin applied the whole time, which is what actually holds the
  // height steady.
  const hasItems = items.length > 0

  // The dot means "what's on screen right now is fresh", not narrowly "the portfolio has
  // live prices" — `priceStatus` alone is that narrower holdings-only signal, computed
  // upstream from usePortfolio() with no idea the market half exists. A brand-new instance
  // with zero holdings has no live PORTFOLIO price by definition, but the commodity/coin/
  // equity/FX rows scrolling right beside the dot are genuinely live — the dot read grey
  // while the numbers next to it were moving. market.length > 0 is a safe proxy: the hook
  // keeps the last good rows on a fetch failure (never resets to empty), so a non-empty
  // array means a real successful fetch landed, not merely "still trying".
  const dotLive = priceStatus === 'live' || market.length > 0

  // Duration scales with item count so the SPEED is constant whether you hold 3 or 30.
  const seconds = Math.max(20, items.length * 3.2)

  return (
    <div className="ticker" aria-label="Market prices">
      <span className="ticker-head" title={asOfLong} aria-label={asOfLong}>
        <span
          className="ticker-dot"
          style={{ background: dotLive ? 'var(--good)' : 'var(--faint)' }}
          aria-hidden="true"
        />
        <span className="ticker-head-txt">{asOf}</span>
      </span>
      {hasItems && (
        <div className="ticker-viewport">
          {/* The list is rendered TWICE inside the track and the track travels -50%, so the
            second copy is exactly under the cursor as the first leaves — a seamless loop with
            no JS. */}
          <div className="ticker-track" style={{ animationDuration: `${seconds}s` }}>
            {[0, 1].map((copy) => (
              <div className="ticker-seq" key={copy} aria-hidden={copy === 1 ? true : undefined}>
                {items.map((it) => (
                  <Item key={`${copy}:${it.key}`} item={it} onOpen={openSymbol} />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
