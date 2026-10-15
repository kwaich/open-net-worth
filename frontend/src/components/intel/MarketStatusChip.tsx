import type { MarketStatus } from '../../types/assetDetail'

// Market-state chip for the asset-detail header: open / pre / post / closed, the
// timezone, and a best-effort "reopens in …" when closed. Data comes from Yahoo's
// marketState (see services/prices.market_status); crypto reads 24/7. Renders nothing
// when the state is unknown so non-Yahoo/unsupported assets stay clean. NOT the exchange
// name — this sits right next to the ticker/exchange subline (AssetDetailView), which
// already carries that; repeating it here read as duplicated (owner-reproduced live,
// 2026-08-31: "PLTR • NASDAQ ● Open NasdaqGS • EDT").

function stateLabel(m: MarketStatus): string {
  if (m.always_open) return '24/7'
  switch (m.state) {
    case 'REGULAR':
      return 'Open'
    case 'PRE':
      return 'Pre-market'
    case 'POST':
      return 'After hours'
    // PREPRE / POSTPOST are deep-overnight — treated as closed (see services/prices).
    case 'PREPRE':
    case 'POSTPOST':
    case 'CLOSED':
      return 'Market closed'
    default:
      return ''
  }
}

function tone(m: MarketStatus): 'open' | 'ext' | 'closed' {
  if (m.is_regular || m.always_open) return 'open'
  if (m.is_open) return 'ext'
  return 'closed'
}

// A single forward-looking countdown to whatever this state transitions to NEXT — reopens
// (closed → next open), opens (pre-market → regular open), or closes (regular/post-market →
// that session's own end). One shared formatter so "reopens in 2h", "opens in 4h 45m", and
// "closes in 12m" all read consistently (owner ask 2026-09-01: consolidate the countdown that
// used to live in the price caption's own text here instead — it was cramping onto 3 ragged
// lines on a narrow phone width).
function countdownLabel(verb: string, iso: string | null | undefined): string | null {
  if (!iso) return null
  const t = new Date(iso).getTime()
  if (isNaN(t)) return null
  const diff = t - Date.now()
  if (diff <= 0) return null
  const mins = Math.round(diff / 60000)
  if (mins < 60) return `${verb} in ${mins}m`
  const h = Math.floor(mins / 60)
  const mm = mins % 60
  if (h < 24) return `${verb} in ${h}h${mm ? ` ${mm}m` : ''}`
  if (verb !== 'reopens') return null // opens/closes is always same-day; a day+ out is stale data
  return `reopens ${new Date(iso).toLocaleString(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit' })}`
}

// Which target time each state counts down to — always the NEXT transition, never a countdown
// to something already past.
function countdownFor(market: MarketStatus): string | null {
  switch (market.state) {
    case 'PRE':
      return countdownLabel('opens', market.session_bounds?.regular?.start)
    case 'REGULAR':
      return countdownLabel('closes', market.session_bounds?.regular?.end)
    case 'POST':
      return countdownLabel('closes', market.session_bounds?.post?.end)
    default:
      // PREPRE / POSTPOST / CLOSED — the only case with a holiday-accurate `reopens_at`.
      return countdownLabel('reopens', market.reopens_at)
  }
}

export function MarketStatusChip({ market }: { market?: MarketStatus | null }) {
  if (!market || !market.state || market.state === 'UNKNOWN') return null
  const label = stateLabel(market)
  if (!label) return null
  const delayed = market.delayed_minutes ? `Delayed ${market.delayed_minutes}m` : null
  const countdown = countdownFor(market)
  // The full text, even when collapsed to a bare dot at narrow widths (see the container
  // query in index.css) — still readable on hover/long-press either way.
  const fullText = [label, market.tz_short, delayed, countdown].filter(Boolean).join(' • ')
  // tz_short and the Delayed tag used to be ONE joined ".asd-market-meta" span. Split into
  // independent spans (owner ask 2026-09-08) so the narrow-header collapse below can drop the
  // tz text with the state label while keeping "Delayed Nm" visible — it's important enough
  // not to hide behind a long-press, unlike the timezone abbreviation.
  return (
    <span className={`asd-market asd-market--${tone(market)}`} title={fullText || undefined}>
      <span className="asd-market-dot" aria-hidden />
      <span className="asd-market-state">{label}</span>
      {market.tz_short && <span className="asd-market-tz">{market.tz_short}</span>}
      {delayed && <span className="asd-market-delayed">{delayed}</span>}
      {countdown && <span className="asd-market-reopen">{countdown}</span>}
    </span>
  )
}
