import { useFixedIncome } from '../../hooks/useFixedIncome'
import { fBase, fDate } from '../../utils/format'
import { CollapsibleSection, SummaryBar, Loading, ErrorState, EmptyState } from '../ui'
import type { StatCard } from '../ui'
import { useBreakpoint } from '../../hooks/useBreakpoint'

interface Props {
  // Bumped by the parent after a bond income event is recorded, so the card refetches.
  version?: number
}

// Human label for each bond subtype badge in the ladder list.
const SUBTYPE_LABEL: Record<string, string> = {
  market: 'Market',
  ssb: 'SSB',
  tbill: 'T-bill',
  etf: 'ETF',
  fd: 'FD',
}

/**
 * BondMaturityCard — a collapsible summary of upcoming maturities and the full ≤365d
 * maturity ladder. Renders nothing when the user holds no bonds (count === 0).
 * Data from GET /api/fixed-income via useFixedIncome.
 */
export function BondMaturityCard({ version }: Props) {
  const { data, loading, error } = useFixedIncome(version)
  const bp = useBreakpoint()
  // Open by default on desktop (md+), collapsed on mobile (<640px = only 'sm' label).
  const defaultOpen = bp !== 'sm'

  // Don't mount until we know there are bonds (avoids a flash on initial load).
  if (!loading && !error && data && data.count === 0) return null

  const cards: StatCard[] = data
    ? [
        {
          label: 'Matures ≤ 30d',
          value: data.maturity_buckets.d30 > 0 ? fBase(data.maturity_buckets.d30) : '—',
        },
        {
          label: 'Matures ≤ 90d',
          value: data.maturity_buckets.d90 > 0 ? fBase(data.maturity_buckets.d90) : '—',
        },
        {
          label: 'Matures ≤ 1y',
          value: data.maturity_buckets.y1 > 0 ? fBase(data.maturity_buckets.y1) : '—',
        },
      ]
    : []

  return (
    <CollapsibleSection
      title="Upcoming maturities & coupons"
      className="bond-maturity-card"
      defaultOpen={defaultOpen}
    >
      {loading && <Loading label="Loading maturity data…" />}

      {!loading && error && <ErrorState message="Couldn't load fixed-income maturity data." />}

      {!loading && !error && data && (
        <>
          {cards.length > 0 && <SummaryBar cards={cards} />}

          {data.maturity_ladder.length === 0 ? (
            <EmptyState
              title="No maturities in the next 12 months."
              body="Bonds and fixed deposits without a maturity date (e.g. bond ETFs) are not shown here."
            />
          ) : (
            <div className="bond-ladder-table">
              <div className="bond-ladder-row bond-ladder-row-head">
                <span className="bond-ladder-date">Matures</span>
                <span className="bond-ladder-name">Holding</span>
                <span className="bond-ladder-type">Type</span>
                <span className="bond-ladder-proceeds">Proceeds ({data.base_currency})</span>
              </div>
              {data.maturity_ladder.map((row) => (
                <div key={row.instrument_id} className="bond-ladder-row">
                  <span className="bond-ladder-date">
                    {row.maturity_date ? fDate(row.maturity_date, 'short', 'always') : '—'}
                  </span>
                  <span className="bond-ladder-name">{row.name}</span>
                  <span className="bond-ladder-type">
                    {SUBTYPE_LABEL[row.subtype] ?? row.subtype}
                  </span>
                  <span className="bond-ladder-proceeds">{fBase(row.proceeds_base)}</span>
                </div>
              ))}
            </div>
          )}
          {(data.ladder_excluded_count ?? 0) > 0 && (
            <p className="bond-ladder-footnote">
              {data.ladder_excluded_count}{' '}
              {data.ladder_excluded_count === 1 ? 'bond fund' : 'bond funds'} without a fixed
              maturity {data.ladder_excluded_count === 1 ? 'is' : 'are'} not shown above.
            </p>
          )}
        </>
      )}
    </CollapsibleSection>
  )
}
