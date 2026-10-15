// luciusEngine.ts — pure-TS port of the Lucius suggestion engine from portfolio-lucius.jsx.
// All global window.INV reads are replaced with explicit typed parameters (LuciusContext).
// Nothing here touches React, the DOM, or the network.

export type AlertOp = 'above' | 'below'

export interface LuciusAsset {
  symbol: string
  name?: string
  ccy: string
  price: number
  cls?: string
  alerts?: { op: AlertOp; value: number; note?: string }[]
}

export interface LuciusHolding {
  symbol: string
  cls: string
  qty?: number
  weight: number
  plPct: number | null
}

export interface LuciusContext {
  holdingsBySymbol: Record<string, LuciusHolding> // owned positions keyed by uppercased symbol
  targets: Record<string, number> // asset_class -> target weight %
  classLabels: Record<string, string> // asset_class -> display label
}

// Default target asset allocation (% of book per class) the suggestion engine reasons against to
// decide trim-vs-accumulate. Hardcoded reference model — NOT yet sourced from the user's profile.
export const DEFAULT_TARGETS: Record<string, number> = {
  equity: 60,
  crypto: 5,
  commodity: 8,
  bond: 24,
  cash: 3,
}

// asset_class key -> human label, used only to phrase suggestion copy ("equities" vs "equity").
export const CLASS_LABELS: Record<string, string> = {
  equity: 'Equities',
  crypto: 'Crypto',
  commodity: 'Commodities',
  bond: 'Bonds',
  cash: 'Cash',
  property: 'Property',
  receivable: 'Receivables',
}

export interface AlertSuggestion {
  op: AlertOp
  value: number
  why: string
  note: string
  /** The alert's own short name when it is taken — e.g. "Take-profit zone". */
  name?: string
}

export interface LuciusReviewItem {
  _id?: string
  id?: string
  kind: 'ticker' | 'topic'
  symbol?: string
  label?: string
  note?: string
  muted?: boolean
  // Only the count is read during review, so the element shape is intentionally loose
  // (callers pass watchlist price_alerts whose op vocabulary differs from AlertOp).
  alerts?: unknown[]
}

export interface LuciusFinding {
  key: string
  itemId?: string
  kind?: 'asset' | 'topic'
  sev: 'gap' | 'risk' | 'info'
  title: string
  text: string
  cta?: string
  noCta?: boolean
}

export interface LuciusReviewResult {
  headline: string
  findings: LuciusFinding[]
}

// Currency symbol map
const LX_CCY: Record<string, string> = { USD: '$', SGD: 'S$', EUR: '€', GBP: '£' }
const lccy = (c: string): string => LX_CCY[c] ?? ''

// Per-class price bands — how far a "meaningful move" is, by volatility
export const LX_BAND: Record<string, number> = {
  equity: 0.12,
  crypto: 0.2,
  commodity: 0.08,
  bond: 0.05,
}

// Round to a "nice" number appropriate for the magnitude
export function lxRound(v: number): number {
  if (v >= 10000) return Math.round(v / 500) * 500
  if (v >= 1000) return Math.round(v / 100) * 100
  if (v >= 100) return Math.round(v / 5) * 5
  if (v >= 10) return Math.round(v * 2) / 2
  return Math.round(v * 100) / 100
}

// Format a number for display — thousands separator above 1000
export function lxFmt(v: number): string {
  return v >= 1000 ? v.toLocaleString('en-US') : String(v)
}

// Percent distance between value and price (absolute), rounded to integer
export function lxDist(value: number, price: number): number {
  return Math.round((Math.abs(value - price) / price) * 100)
}

// ---- suggestion engine ----

export function luciusSuggestAlerts(asset: LuciusAsset, ctx: LuciusContext): AlertSuggestion[] {
  // No live price yet → every level/distance math divides by zero (NaN% in the UI).
  if (!asset.price || asset.price <= 0) return []
  const H = ctx.holdingsBySymbol[asset.symbol.toUpperCase()] ?? null
  const cls = H ? H.cls : (asset.cls ?? 'equity')
  const band = LX_BAND[cls] ?? 0.12
  const price = asset.price
  const ccy = lccy(asset.ccy)
  const existing = (asset.alerts ?? []).map((a) => Number(a.value))
  const tooClose = (v: number) => existing.some((e) => Math.abs(e - v) / price < 0.025)
  const out: AlertSuggestion[] = []
  const unit = cls === 'crypto' ? 'units' : 'shares'

  if (H !== null) {
    const target = ctx.targets[cls] ?? 0
    const cost = H.plPct != null ? price / (1 + H.plPct / 100) : price
    const over = H.weight - target

    // upside suggestion
    const up = lxRound(price * (1 + band))
    if (over > 1.5) {
      const trimShares = H.qty != null ? Math.max(1, Math.round(H.qty * (over / H.weight))) : null
      const clsLabel = ctx.classLabels[cls]?.toLowerCase() ?? cls
      out.push({
        name: 'Trim back to target',
        op: 'above',
        value: up,
        why: `At ${H.weight.toFixed(1)}% of your book, ${asset.symbol} is already over your ${target}% ${clsLabel} target.`,
        note: `If it rises ~${lxDist(up, price)}% to ${ccy}${lxFmt(up)}, trim back toward ${target}%${trimShares != null ? ` (~${trimShares} ${unit})` : ''} rather than letting the position get bigger. Check first whether the move is news or momentum.`,
      })
    } else {
      out.push({
        name: 'Take-profit zone',
        op: 'above',
        value: up,
        why: `${ccy}${lxFmt(up)} is ~${lxDist(up, price)}% above today — a level worth banking some profit.`,
        note: `Review taking partial profit on ${asset.symbol}; confirm the move isn't a single-day spike before acting.`,
      })
    }

    // downside suggestion
    if (H.plPct != null && H.plPct > 25) {
      const dn = lxRound(price * (1 - band))
      out.push({
        name: 'Protect the gain',
        op: 'below',
        value: dn,
        why: `You're up ${H.plPct.toFixed(0)}% on ${asset.symbol} — this protects the gain (~${lxDist(dn, price)}% below today).`,
        note: `If it falls to ${ccy}${lxFmt(dn)} and support breaks, consider a trailing stop or trimming to lock in profit.`,
      })
    } else if (H.plPct != null && H.plPct < -3) {
      const dn = lxRound(cost * 0.9)
      out.push({
        name: 'Decision point',
        op: 'below',
        value: dn,
        why: `${asset.symbol} is down ${Math.abs(H.plPct).toFixed(0)}%; ~10% below your cost (${ccy}${lxFmt(dn)}) is a clear decision point.`,
        note: `If it reaches ${ccy}${lxFmt(dn)}, decide whether the thesis still holds before averaging down or cutting.`,
      })
    } else {
      const dn = lxRound(price * (1 - band))
      out.push({
        name: 'Early warning',
        op: 'below',
        value: dn,
        why: `${ccy}${lxFmt(dn)} is ~${lxDist(dn, price)}% below today — an early warning if it rolls over.`,
        note: `If ${asset.symbol} drops to ${ccy}${lxFmt(dn)}, check for thesis-breaking news and decide to hold, add, or cut.`,
      })
    }

    // crypto stretch level
    if (cls === 'crypto') {
      const target = ctx.targets[cls] ?? 0
      const stretch = lxRound(price * (1 + band * 2))
      out.push({
        name: 'Stretch target',
        op: 'above',
        value: stretch,
        why: `A stretch target ~${lxDist(stretch, price)}% up — crypto can move fast and overshoot.`,
        note: `If ${asset.symbol} spikes to ${ccy}${lxFmt(stretch)}, take meaningful profit and rebalance crypto back to your ${target}% target.`,
      })
    }
  } else {
    // not owned — entry levels
    const dn = lxRound(price * (1 - band))
    out.push({
      name: 'Entry zone',
      op: 'below',
      value: dn,
      why: `You don't own ${asset.symbol}. ${ccy}${lxFmt(dn)} (~${lxDist(dn, price)}% lower) would be a more attractive entry.`,
      note: `If it dips to ${ccy}${lxFmt(dn)}, assess whether it's a buying opportunity or a broken setup before starting a position.`,
    })
    const up = lxRound(price * (1 + band * 0.7))
    out.push({
      name: 'Momentum break',
      op: 'above',
      value: up,
      why: `A break above ${ccy}${lxFmt(up)} would confirm momentum if you're waiting to enter.`,
      note: `On a confirmed break, consider a starter position sized against the rest of the book.`,
    })
  }

  return out.filter((s) => !tooClose(s.value))
}

// ---- alert instruction drafter ----

export function luciusDraftAlertInstruction(
  asset: LuciusAsset,
  row: { op: AlertOp; value?: number | string },
  owned: boolean,
): string {
  const ccy = lccy(asset.ccy)
  const val = row.value != null ? `${ccy}${lxFmt(Number(row.value))}` : 'this level'
  if (row.op === 'above') {
    return owned
      ? `If ${asset.symbol} reaches ${val}, review taking partial profit — check whether the move is news-driven, and consider trimming toward your target weight.`
      : `If ${asset.symbol} breaks ${val}, consider a starter position on confirmed momentum; size it against the rest of the book.`
  }
  return owned
    ? `If ${asset.symbol} falls to ${val}, reassess the thesis before holding through — decide whether to add, hold, or cut.`
    : `If ${asset.symbol} dips to ${val}, assess whether it's an entry opportunity or a broken setup before buying.`
}

// ---- topic instruction drafter ----

export function luciusDraftTopicInstruction(label: string): string {
  const l = (label ?? '').toLowerCase()
  let mid =
    'Flag anything material to my portfolio — holdings directly affected, sector or macro spillover, and clear risk-on/risk-off signals.'
  if (/rate|fed|inflation|cpi|yield|fomc/.test(l))
    mid =
      'Flag rate-path shifts and inflation surprises, and what they mean for my bond sleeve (TLT, SSB) and rate-sensitive equities.'
  else if (/oil|energy|opec|gas|crude/.test(l))
    mid =
      'Flag supply or price shocks and the knock-on for inflation, energy names, and broad risk appetite.'
  else if (/ai|chip|semi|nvidia|gpu|datacenter|data center/.test(l))
    mid =
      'Flag demand, supply, and policy shifts, and the impact on my NVDA, MSFT, and ASML positions.'
  else if (/crypto|bitcoin|\bbtc\b|\beth\b|etf flow|stablecoin/.test(l))
    mid =
      'Flag ETF flows, regulation, and liquidity shifts, and the impact on my BTC and ETH positions and overall crypto weight.'
  else if (/war|conflict|geopolit|sanction|tension|hormuz|tariff/.test(l))
    mid =
      'Flag escalation or de-escalation: safe-haven gold bid, oil spikes, shipping disruption, and risk-off equity moves.'
  return `Track developments on ${label}. ${mid} Summarise what changed, why it matters to my book, and any action worth considering.`
}

// ---- list reviewer ----

export function luciusReviewList(
  list: { name: string; muted?: boolean; items: LuciusReviewItem[] },
  ctx: LuciusContext,
): LuciusReviewResult {
  const findings: LuciusFinding[] = []
  const assets = list.items.filter((i) => i.kind === 'ticker')
  const topics = list.items.filter((i) => i.kind === 'topic')

  if (list.muted) {
    findings.push({
      key: 'muted',
      sev: 'info',
      title: 'This list is muted',
      text: 'Nothing here will alert you until you unmute the list.',
      noCta: true,
    })
  }

  for (const a of assets) {
    if (a.muted) continue
    const itemId = a._id ?? a.id
    const sym = (a.symbol ?? '').toUpperCase()
    const H = ctx.holdingsBySymbol[sym] ?? null
    if (!(a.alerts ?? []).length) {
      findings.push({
        key: itemId ?? sym,
        itemId,
        kind: 'asset',
        sev: 'gap',
        title: `${a.symbol} has no price alerts`,
        text: H
          ? `You hold this but won't be notified on a meaningful move.`
          : `You won't be notified on a meaningful move.`,
        cta: 'Suggest alerts',
      })
    } else if (H != null) {
      const target = ctx.targets[H.cls] ?? 0
      if (H.weight > target + 3) {
        findings.push({
          key: (itemId ?? sym) + ':ow',
          itemId,
          kind: 'asset',
          sev: 'risk',
          title: `${a.symbol} is overweight`,
          text: `${H.weight.toFixed(1)}% of your book vs a ${target}% target — a trim alert would help.`,
          cta: 'Add alert',
        })
      }
    }
  }

  for (const t of topics) {
    const itemId = t._id ?? t.id
    if ((t.note ?? '').trim().length < 40) {
      findings.push({
        key: itemId ?? t.label ?? 'topic',
        itemId,
        kind: 'topic',
        sev: 'gap',
        title: `"${t.label}" needs instructions`,
        text: 'Vague topics produce noisy alerts. Lucius can draft focused instructions.',
        cta: 'Draft instructions',
      })
    }
  }

  const actionable = findings.filter((f) => !f.noCta).length
  const headline =
    actionable === 0
      ? `${list.name} looks well-covered — tickers have alerts and topics are specific.`
      : `${actionable} thing${actionable === 1 ? '' : 's'} to tighten up in ${list.name}.`

  return { headline, findings }
}
