// `value_base` hydration for a funding_plan + best-effort Planning-envelope accrual — the
// real GET /api/commitments* endpoints never attach a resolved base value to a funding
// source (that math — services.commitments.live_source_value — only runs internally for
// coverage()/projected_events()), so the detail screen resolves it itself, client-side, over
// data it already fetches. Every funding source (including asset_sale) stores its own frozen
// amount+currency — the plan never re-derives from a live price (owner decision, 2026-07-26:
// "the funding shouldn't be drifted by the market price, unless it's intentional"). Positions
// are still fetched here for DISPLAY only (the live "340 × €38.97 live" annotation in
// fundingWhen() and the AssetLink deep-link) — never for valuation. See docs/adr/2026-07-22-
// commitments-tab-redesign-faithful-port.md.
import { useEffect, useMemo, useState } from 'react'
import type { Commitment, FundingSource } from '../../../types/commitments'
import { authFetch } from '../../../api/client'
import { toBase } from '../../../utils/format'
import type { EnvelopeAccrual } from '../../../utils/commitments'
import { PORTFOLIO_KEY, fetchJson } from '../../../hooks/serverState'
import { fetchCachedQuery } from '../../../state/queryCache'

export interface PositionMeta {
  instrumentId: string
  symbol: string
  quantity: number
  /** Native per-unit price + its currency (display only — "120 × S$38.97 live"). */
  price: number
  priceCurrency: string
}

interface PortfolioRow {
  position_id?: string
  instrument_id?: string
  symbol?: string
  ticker?: string
  price?: string | number
  quantity?: string | number
  quote_per_unit?: number
  quote_currency?: string
  currency?: string
}

/** Base-currency value one funding source contributes — ALWAYS from its own stored
 *  amount+currency (the frozen plan), never live-priced. `asset_sale` sources store a real
 *  amount+currency too (the target the user actually intends to raise), exactly like a fixed
 *  source (cash/income/loan/windfall) — `sell_quantity` is informational context only (how
 *  many shares that was expected to take), not the value source. The plan must not silently
 *  drift with the market unless the user intentionally edits it (owner decision, 2026-07-26 —
 *  mirrors `services.commitments.live_source_value` on the backend). A `disabled` source (the
 *  "not happening" flow) still resolves a value here — callers that must exclude it (the
 *  funding waterfall, the coverage bar) filter on `disabled` themselves, same as the server. */
export function resolveFundingValueBase(
  f: FundingSource,
  commitmentCurrency: string,
  fx: Record<string, number>,
): number {
  if (f.value_base != null) return f.value_base
  return f.amount != null ? toBase(f.amount, f.currency || commitmentCurrency, fx) : 0
}

function hydrateFundingPlan(c: Commitment, fx: Record<string, number>): Commitment {
  return {
    ...c,
    funding_plan: (c.funding_plan || []).map((f) => ({
      ...f,
      value_base: resolveFundingValueBase(f, c.currency, fx),
    })),
  }
}

export interface EnvelopeAccrualState extends EnvelopeAccrual {
  /** assigned + needed_this_month — what the envelope's OWN target wants set aside this
   *  period; used only for the "Synced target" progress meter (real field, not fabricated). */
  target_this_month: number
}

interface PlanningMonthRow {
  envelope_id: string
  label: string
  assigned: number
  available: number
  needed_this_month?: number
}
interface PlanningMonthResponse {
  groups?: Array<{ categories?: PlanningMonthRow[] }>
}

/** Hydrates a commitment's funding_plan with a live `value_base` (see above) and best-effort
 *  fetches the linked Planning envelope's current-month figures for the "Synced target" panel.
 *  Both fetches are lazy (skipped when the commitment doesn't need them) and best-effort — a
 *  failed fetch just leaves that data null/zero rather than blocking the screen; per the task
 *  spec, a caller with no envelope data omits the panel entirely rather than fabricating one. */
export function useCommitmentDetailData(
  commitment: Commitment,
  fx: Record<string, number>,
): {
  hydrated: Commitment
  positions: Record<string, PositionMeta>
  envelope: EnvelopeAccrualState | null
} {
  const [positions, setPositions] = useState<Record<string, PositionMeta>>({})
  const [envelope, setEnvelope] = useState<EnvelopeAccrualState | null>(null)

  const needsPositions = (commitment.funding_plan || []).some(
    (f) => f.type === 'asset_sale' && f.ref_position_id,
  )

  useEffect(() => {
    if (!needsPositions) return
    let cancelled = false
    fetchCachedQuery<PortfolioRow[]>(PORTFOLIO_KEY, () => fetchJson<PortfolioRow[]>(PORTFOLIO_KEY))
      .catch(() => null)
      .then((rows: PortfolioRow[] | null) => {
        if (cancelled || !rows) return
        const map: Record<string, PositionMeta> = {}
        for (const r of rows) {
          if (!r.position_id) continue
          const price = Number(r.price) || 0
          const qpu = Number(r.quote_per_unit) || 1
          const ccy = r.quote_currency || r.currency || ''
          map[r.position_id] = {
            instrumentId: r.instrument_id || '',
            symbol: r.symbol || r.ticker || '',
            quantity: Number(r.quantity) || 0,
            price: price * qpu,
            priceCurrency: ccy,
          }
        }
        setPositions(map)
      })
      .catch(() => {
        /* best-effort — a missing price just means an unresolved asset_sale value (0) */
      })
    return () => {
      cancelled = true
    }
  }, [commitment.id, needsPositions])

  useEffect(() => {
    if (!commitment.envelope_id) {
      setEnvelope(null)
      return
    }
    let cancelled = false
    const ym = new Date().toISOString().slice(0, 7)
    authFetch(`/api/planning/month?ym=${ym}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: PlanningMonthResponse | null) => {
        if (cancelled || !data) return
        const rows = (data.groups || []).flatMap((g) => g.categories || [])
        const row = rows.find((r) => r.envelope_id === commitment.envelope_id)
        setEnvelope(
          row
            ? {
                envelope: row.label,
                per_period: row.assigned,
                accumulated: row.available,
                target_this_month: row.assigned + (row.needed_this_month || 0),
              }
            : null,
        )
      })
      .catch(() => {
        /* best-effort — the "Synced target" panel is omitted without this data */
      })
    return () => {
      cancelled = true
    }
  }, [commitment.id, commitment.envelope_id])

  const hydrated = useMemo(() => hydrateFundingPlan(commitment, fx), [commitment, fx])

  return { hydrated, positions, envelope }
}
