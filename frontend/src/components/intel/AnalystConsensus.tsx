import { useState } from 'react'
import { Panel, Skeleton } from '../ui'
import { ArrowRight } from '../../ui/icons'
import { fDate, getCurrencySymbol } from '../../utils/format'
import type { AnalystResponse } from '../../hooks/useAnalystConsensus'

interface Props {
  /** Prefetched + cached analyst data (from useAnalystConsensus). */
  data: AnalystResponse | null
  /** True while an uncached fetch is in flight — drives the skeleton. */
  loading: boolean
  currency: string
  /** Split for Analysis layout: estimates (top) vs ratings table (bottom). Default both. */
  section?: 'estimates' | 'ratings' | 'all'
}

const DASH = '—'
const RATINGS_PREVIEW = 10

function fMoney(n: number | undefined, sym: string): string {
  if (n == null) return DASH
  return `${n < 0 ? '-' : ''}${sym}${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`
}

/** Rating → tone: buys are good (green), hold neutral (amber), sells bad (red). */
function ratingTone(key?: string): 'good' | 'warn' | 'bad' {
  if (key === 'strong_buy' || key === 'buy') return 'good'
  if (key === 'sell' || key === 'strong_sell' || key === 'underperform') return 'bad'
  return 'warn'
}

/** Per-firm grade string (free text from Yahoo) → tone. Covers the common Street vocab. */
function gradeTone(grade?: string): 'good' | 'warn' | 'bad' {
  const s = (grade || '').toLowerCase()
  if (/(strong buy|buy|outperform|overweight|accumulate|positive|^add$)/.test(s)) return 'good'
  if (/(sell|underperform|underweight|reduce|negative)/.test(s)) return 'bad'
  return 'warn'
}

function clampPct(n: number): number {
  return Math.max(0, Math.min(100, n))
}

// Analyst consensus + 52-week price targets (Yahoo) — Perplexity's "Analysis" cards.
// Self-hiding: renders nothing for assets with no analyst coverage (crypto / commodities).
// Data is fetched + cached by useAnalystConsensus (prefetched at the detail-view level).
export function AnalystConsensus({ data, loading, currency, section = 'all' }: Props) {
  const [showAllRatings, setShowAllRatings] = useState(false)
  const showEstimates = section === 'estimates' || section === 'all'
  const showRatings = section === 'ratings' || section === 'all'

  // Uncached fetch in flight: layout-shaped skeleton so real cards fade into the
  // same slots when the prefetch lands (no pop-in / jump on tab switch).
  if (loading) return <AnalystConsensusSkeleton section={section} />

  const analyst = data?.analyst
  if (!analyst || Object.keys(analyst).length === 0) return null

  const sym = getCurrencySymbol(data?.currency || currency)
  const dist = analyst.distribution
  const bullish = dist ? dist.strong_buy + dist.buy : 0
  const neutral = dist ? dist.hold : 0
  const bearish = dist ? dist.sell + dist.strong_sell : 0
  const total = bullish + neutral + bearish

  const bars: { label: string; value: number; mod: 'bull' | 'neutral' | 'bear' }[] = [
    { label: 'Bullish', value: bullish, mod: 'bull' },
    { label: 'Neutral', value: neutral, mod: 'neutral' },
    { label: 'Bearish', value: bearish, mod: 'bear' },
  ]

  const { target_low: lo, target_mean: avg, target_high: hi, current } = analyst
  const hasTargets = lo != null && hi != null && hi > lo
  const meanPos = hasTargets && avg != null ? clampPct(((avg - lo) / (hi - lo)) * 100) : null
  const curPos = hasTargets && current != null ? clampPct(((current - lo) / (hi - lo)) * 100) : null
  const upTone =
    analyst.upside_pct == null
      ? ''
      : analyst.upside_pct >= 0
        ? 'asd-target-upside--good'
        : 'asd-target-upside--bad'

  // Legend ordered by actual value, so Current sits where it truly falls (e.g. right of
  // Average when it's higher). Low/High anchor the ends only when their value is unknown.
  const targetLegend = [
    { key: 'low', label: 'Low', value: lo, sort: lo ?? -Infinity },
    ...(current != null
      ? [{ key: 'current', label: 'Current', value: current, sort: current }]
      : []),
    ...(avg != null ? [{ key: 'mean', label: 'Average', value: avg, sort: avg }] : []),
    { key: 'high', label: 'High', value: hi, sort: hi ?? Infinity },
  ].sort((a, b) => a.sort - b.sort)

  // Per-firm ratings table (Yahoo upgrades_downgrades). Upside is derived per row vs the
  // current price. Yahoo gives the firm + target + date, but no analyst name or source link.
  const ratings = data?.ratings ?? []
  const visibleRatings = showAllRatings ? ratings : ratings.slice(0, RATINGS_PREVIEW)
  const rowUpside = (target?: number): number | null =>
    target != null && current ? (target / current - 1) * 100 : null

  return (
    <>
      {showEstimates && (
        <Panel>
          <div className="asd-section-head">
            <span className="asd-section-title">Analyst Estimates</span>
            {analyst.analyst_count != null && (
              <span className="asd-analyst-count">Based on {analyst.analyst_count} analysts</span>
            )}
          </div>
          <div className="asd-analyst-grid">
            {/* Consensus rating + bullish/neutral/bearish distribution */}
            <div className="asd-analyst-card">
              <span className="asd-analyst-card-title">Analyst Consensus</span>
              {analyst.rating_label && (
                <span
                  className={`asd-consensus-rating asd-consensus-rating--${ratingTone(analyst.rating_key)}`}
                >
                  {analyst.rating_label}
                </span>
              )}
              {total > 0 && (
                <div className="asd-consensus-bars">
                  {bars.map((b) => {
                    const pct = total ? Math.round((b.value / total) * 100) : 0
                    return (
                      <div key={b.label} className="asd-consensus-bar-row">
                        <span className="asd-consensus-bar-label">{b.label}</span>
                        <span className="asd-consensus-bar-track">
                          <span
                            className={`asd-consensus-bar-fill asd-consensus-bar-fill--${b.mod}`}
                            style={{ width: `${pct}%` }}
                          />
                        </span>
                        <span className="asd-consensus-bar-meta">
                          {b.value} • {pct}%
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {/* 52-week price targets: low — current — avg — high range */}
            <div className="asd-analyst-card">
              <span className="asd-analyst-card-title">52W Price Targets</span>
              <div className="asd-target-headline">
                <span className="asd-target-avg">{fMoney(avg, sym)}</span>
                <span className="asd-target-avg-label">Avg target</span>
                {analyst.upside_pct != null && (
                  <span className={`asd-target-upside ${upTone}`}>
                    {analyst.upside_pct >= 0 ? '+' : ''}
                    {analyst.upside_pct.toFixed(1)}%{' '}
                    {analyst.upside_pct >= 0 ? 'upside' : 'downside'}
                  </span>
                )}
              </div>
              {hasTargets && (
                <div className="asd-target-bar">
                  <span className="asd-target-track" />
                  {curPos != null && (
                    <span
                      className="asd-target-marker asd-target-marker--current"
                      style={{ left: `${curPos}%` }}
                      title={`Current ${fMoney(current, sym)}`}
                    />
                  )}
                  {meanPos != null && (
                    <span
                      className="asd-target-marker asd-target-marker--mean"
                      style={{ left: `${meanPos}%` }}
                      title={`Average ${fMoney(avg, sym)}`}
                    />
                  )}
                </div>
              )}
              {/* Labeled-dot legend (Perplexity-style): each value maps to its dot colour on the
              track, ordered by actual value so the label never implies a fixed position. */}
              <div className="asd-target-legend">
                {targetLegend.map((it) => (
                  <div key={it.key} className="asd-target-leg">
                    <span className="asd-target-leg-value">{fMoney(it.value, sym)}</span>
                    <span className="asd-target-leg-label">
                      <span className={`asd-target-dot asd-target-dot--${it.key}`} />
                      {it.label}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Panel>
      )}

      {showRatings && ratings.length > 0 && (
        <Panel>
          <div className="asd-section-head">
            <span className="asd-section-title">Analyst Ratings</span>
            <span className="asd-analyst-count">Firm • rating • target — via Yahoo</span>
          </div>
          <div className="asd-ratings">
            <div className="asd-ratings-row asd-ratings-row--head">
              <span>Firm</span>
              <span className="asd-rating-cell">Rating</span>
              <span className="asd-ratings-num">Price Target</span>
              <span className="asd-ratings-num">Upside</span>
              <span className="asd-ratings-num">Date</span>
            </div>
            {visibleRatings.map((r, i) => {
              const up = rowUpside(r.target)
              return (
                <div key={`${r.firm}-${r.date}-${i}`} className="asd-ratings-row">
                  <span className="asd-rating-firm">{r.firm}</span>
                  <span className="asd-rating-cell">
                    {r.rating ? (
                      <span className={`asd-rating-pill asd-rating-pill--${gradeTone(r.rating)}`}>
                        {r.rating}
                      </span>
                    ) : (
                      DASH
                    )}
                  </span>
                  <span className="asd-ratings-num asd-rating-target">
                    {r.prior_target != null && r.prior_target !== r.target && (
                      <span className="asd-rating-prior">{fMoney(r.prior_target, sym)} →</span>
                    )}
                    <span>{r.target != null ? fMoney(r.target, sym) : DASH}</span>
                  </span>
                  <span
                    className={`asd-ratings-num asd-rating-upside${up == null ? '' : up >= 0 ? ' asd-rating-upside--good' : ' asd-rating-upside--bad'}`}
                  >
                    {up == null ? DASH : `${up >= 0 ? '+' : ''}${up.toFixed(0)}%`}
                  </span>
                  <span className="asd-ratings-num asd-rating-date">
                    {fDate(r.date, 'short', 'always')}
                  </span>
                </div>
              )
            })}
          </div>
          {ratings.length > RATINGS_PREVIEW && (
            <button
              type="button"
              className="asd-view-all-btn asd-ratings-more"
              onClick={() => setShowAllRatings((v) => !v)}
            >
              {showAllRatings ? 'Show less' : `See ${ratings.length - RATINGS_PREVIEW} more`}
            </button>
          )}
        </Panel>
      )}
    </>
  )
}

interface RailCardProps {
  data: AnalystResponse | null
  loading: boolean
  currency: string
  /** Jumps to the full Analysis-tab breakdown — omit to hide the link. */
  onSeeAll?: () => void
}

/** Compact combined card for the Overview rail: rating + bull/neutral/bear distribution +
 *  52W price-target range in ONE card, unlike the Analysis tab's two separate panels above
 *  (there's room there for the fuller layout; the rail is narrow). */
export function AnalystConsensusRailCard({ data, loading, currency, onSeeAll }: RailCardProps) {
  if (loading) return <AnalystConsensusRailSkeleton />

  const analyst = data?.analyst
  if (!analyst || Object.keys(analyst).length === 0) return null

  const sym = getCurrencySymbol(data?.currency || currency)
  const dist = analyst.distribution
  const bullish = dist ? dist.strong_buy + dist.buy : 0
  const neutral = dist ? dist.hold : 0
  const bearish = dist ? dist.sell + dist.strong_sell : 0
  const total = bullish + neutral + bearish

  const { target_low: lo, target_mean: avg, target_high: hi, current } = analyst
  const hasTargets = lo != null && hi != null && hi > lo
  const meanPos = hasTargets && avg != null ? clampPct(((avg - lo) / (hi - lo)) * 100) : null
  const curPos = hasTargets && current != null ? clampPct(((current - lo) / (hi - lo)) * 100) : null
  const targetLegend = [
    { key: 'low', label: 'Low', value: lo, sort: lo ?? -Infinity },
    ...(current != null
      ? [{ key: 'current', label: 'Current', value: current, sort: current }]
      : []),
    ...(avg != null ? [{ key: 'mean', label: 'Average', value: avg, sort: avg }] : []),
    { key: 'high', label: 'High', value: hi, sort: hi ?? Infinity },
  ].sort((a, b) => a.sort - b.sort)

  const seeAllAction = onSeeAll && (
    <button type="button" className="asd-rail-manage" onClick={onSeeAll}>
      See all <ArrowRight size={12} />
    </button>
  )

  return (
    <Panel title="Analyst Consensus" actions={seeAllAction} className="asd-rail-panel">
      <div className="asd-consensus-rail">
        {analyst.rating_label && (
          <div className="asd-consensus-rail-meta">
            <span
              className={`asd-consensus-rating asd-consensus-rating--${ratingTone(analyst.rating_key)}`}
            >
              {analyst.rating_label}
            </span>
            {analyst.analyst_count != null && (
              <span className="asd-analyst-count">{analyst.analyst_count} analysts</span>
            )}
          </div>
        )}
        {total > 0 && (
          <>
            <div className="asd-consensus-rail-counts">
              <span className="asd-consensus-rail-count asd-consensus-rail-count--bear">
                {bearish} Bearish
              </span>
              <span className="asd-consensus-rail-count asd-consensus-rail-count--neutral">
                {neutral} Neutral
              </span>
              <span className="asd-consensus-rail-count asd-consensus-rail-count--bull">
                {bullish} Bullish
              </span>
            </div>
            <div className="asd-consensus-rail-bar">
              {bearish > 0 && (
                <span
                  className="asd-consensus-rail-seg asd-consensus-rail-seg--bear"
                  style={{ flexGrow: bearish }}
                />
              )}
              {neutral > 0 && (
                <span
                  className="asd-consensus-rail-seg asd-consensus-rail-seg--neutral"
                  style={{ flexGrow: neutral }}
                />
              )}
              {bullish > 0 && (
                <span
                  className="asd-consensus-rail-seg asd-consensus-rail-seg--bull"
                  style={{ flexGrow: bullish }}
                />
              )}
            </div>
          </>
        )}
        {hasTargets && (
          <>
            <div className="asd-target-bar">
              <span className="asd-target-track" />
              {curPos != null && (
                <span
                  className="asd-target-marker asd-target-marker--current"
                  style={{ left: `${curPos}%` }}
                  title={`Current ${fMoney(current, sym)}`}
                />
              )}
              {meanPos != null && (
                <span
                  className="asd-target-marker asd-target-marker--mean"
                  style={{ left: `${meanPos}%` }}
                  title={`Average ${fMoney(avg, sym)}`}
                />
              )}
            </div>
            <div className="asd-target-legend">
              {targetLegend.map((it) => (
                <div key={it.key} className="asd-target-leg">
                  <span className="asd-target-leg-value">{fMoney(it.value, sym)}</span>
                  <span className="asd-target-leg-label">
                    <span className={`asd-target-dot asd-target-dot--${it.key}`} />
                    {it.label}
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </Panel>
  )
}

function AnalystConsensusRailSkeleton() {
  return (
    <Panel className="asd-rail-panel">
      <div className="asd-consensus-rail">
        <Skeleton width={90} height={22} radius="var(--radius-md)" />
        <Skeleton height={8} radius="var(--radius-pill)" />
        <div className="asd-skel-row">
          <Skeleton width={50} height={10} />
          <Skeleton width={50} height={10} />
          <Skeleton width={50} height={10} />
        </div>
      </div>
    </Panel>
  )
}

/** Skeleton for the Analysis tab — mirrors the Estimates grid + a few ratings rows. */
function AnalystConsensusSkeleton({
  section = 'all',
}: {
  section?: 'estimates' | 'ratings' | 'all'
}) {
  const showEstimates = section === 'estimates' || section === 'all'
  const showRatings = section === 'ratings' || section === 'all'
  return (
    <>
      {showEstimates && (
        <Panel>
          <div className="asd-section-head">
            <Skeleton width={150} height={13} />
          </div>
          <div className="asd-analyst-grid">
            <div className="asd-analyst-card">
              <Skeleton width={120} height={11} />
              <Skeleton width={90} height={22} radius="var(--radius-md)" />
              <div className="asd-skel-stack">
                <Skeleton height={10} />
                <Skeleton height={10} />
                <Skeleton height={10} />
              </div>
            </div>
            <div className="asd-analyst-card">
              <Skeleton width={120} height={11} />
              <Skeleton width={140} height={22} radius="var(--radius-md)" />
              <Skeleton height={8} radius="var(--radius-pill)" />
              <div className="asd-skel-row">
                <Skeleton width={50} height={10} />
                <Skeleton width={50} height={10} />
                <Skeleton width={50} height={10} />
              </div>
            </div>
          </div>
        </Panel>
      )}
      {showRatings && (
        <Panel>
          <div className="asd-section-head">
            <Skeleton width={120} height={13} />
          </div>
          <div className="asd-skel-stack">
            {Array.from({ length: 5 }, (_, i) => (
              <Skeleton key={i} height={16} />
            ))}
          </div>
        </Panel>
      )}
    </>
  )
}
