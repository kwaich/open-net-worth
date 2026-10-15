import type { ReactNode } from 'react'
import { Panel, Skeleton } from '../ui'

/**
 * Skeleton of the "Stories & Analysis" panel — mirrors the real news grid so it
 * occupies the same slots. Shared by the initial-load skeleton AND the in-place
 * "fetching news" state on refresh, so neither shows a bare spinner.
 *
 * `header` replaces the placeholder title bar with live text (e.g. the rotating
 * "researching…" status) — passed on refresh, omitted on plain initial load.
 */
export function NewsSectionSkeleton({ header }: { header?: ReactNode }) {
  return (
    <Panel>
      <div className="asd-section-head">{header ?? <Skeleton width={160} height={13} />}</div>
      <div className="asd-news-grid">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="asd-news-card asd-news-card--skel">
            <div className="asd-news-card-body asd-skel-stack">
              <Skeleton height={13} />
              <Skeleton width="80%" height={13} />
              <Skeleton width={120} height={10} style={{ marginTop: 'var(--space-2)' }} />
            </div>
          </div>
        ))}
      </div>
    </Panel>
  )
}

interface SkeletonProps {
  /** Known instrument name (seeded from the list we navigated from) — shown real,
   *  so the header isn't a gray bar while the body loads. */
  name?: string
  /** Known ticker/code, shown as the subline when available. */
  ticker?: string
  /** Real Overview/Analysis sub-tab control — static nav labels, so they render for
   *  real (and stay clickable) during load rather than shimmering as a placeholder. */
  subTabs?: ReactNode
}

/**
 * Initial-load placeholder for AssetDetailView. Mirrors the Overview layout
 * (header → subtabs → price → chart + news main column, position/facts/peers
 * rail) using the real layout classes, so when data lands the content fades
 * into the SAME grid slots — no spinner-to-content jump. Any data we already
 * have (name/code) renders for real — only the unknown parts shimmer.
 */
export function AssetDetailSkeleton({ name, ticker, subTabs }: SkeletonProps = {}) {
  return (
    <div className="asd-root" role="status" aria-busy="true" aria-label="Loading asset detail">
      {/* Header: name + subline • follow/refresh */}
      <div className="asd-header">
        <div className="asd-header-left">
          {name ? (
            <div className="asd-header-text">
              <h2 className="asd-asset-name">{name}</h2>
              {ticker ? (
                <div className="asd-asset-subline">{ticker}</div>
              ) : (
                <Skeleton width={150} height={13} style={{ marginTop: 'var(--space-2)' }} />
              )}
            </div>
          ) : (
            <div className="asd-header-text asd-skel-stack">
              <Skeleton width={240} height={26} />
              <Skeleton width={150} height={13} />
            </div>
          )}
        </div>
        {/* Mirrors the REAL buttons' own icon + collapsible-label shape (`.ui-btn`,
            `.asd-wl-label`/`.asd-refresh-btn-label`) rather than two fixed-size boxes —
            those collapse to icon-only under the exact same container query real content
            does (index.css), so the skeleton shrinks/grows in step with it instead of
            being replaced by a differently-sized real button once data lands
            (owner-reproduced live bug, 2026-08-31: "the header got wrapped and then
            unwrapped" — an oversized fixed skeleton pushed the title to wrap, then a much
            smaller real icon-only button let it un-wrap on load). */}
        <div className="asd-header-right">
          <span className="ui-btn ui-btn--sm ui-btn--ghost" aria-hidden="true">
            <Skeleton width={14} height={14} radius="50%" />
            <span className="asd-wl-label">
              <Skeleton width={58} height={12} />
            </span>
          </span>
          <span className="ui-btn ui-btn--sm ui-btn--ghost" aria-hidden="true">
            <Skeleton width={14} height={14} radius="50%" />
            <span className="asd-refresh-btn-label">
              <Skeleton width={46} height={12} />
            </span>
          </span>
        </div>
      </div>

      {/* Sub-tab strip — real control when provided (static nav), else a placeholder. */}
      <div className="asd-subtabs">
        {subTabs ?? <Skeleton width={200} height={32} radius="var(--radius-pill)" />}
      </div>

      {/* Hero price */}
      <div className="asd-price-block">
        <Skeleton width={150} height={32} />
        <Skeleton width={64} height={22} radius="var(--radius-pill)" />
      </div>

      {/* Body grid */}
      <div className="asd-body">
        <div className="asd-main">
          {/* Chart + quote stats */}
          <Panel>
            <div className="asd-skel-row">
              <Skeleton width={210} height={28} radius="var(--radius-pill)" />
            </div>
            <Skeleton
              height={240}
              radius="var(--radius-md)"
              style={{ marginTop: 'var(--space-5)' }}
            />
            <div className="asd-skel-stat-grid">
              {Array.from({ length: 6 }, (_, i) => (
                <Skeleton key={i} height={12} />
              ))}
            </div>
          </Panel>

          {/* Stories & Analysis */}
          <NewsSectionSkeleton />
        </div>

        {/* Right rail */}
        <div className="asd-rail">
          {[160, 200, 120].map((titleW, idx) => (
            <Panel key={idx}>
              <div className="asd-skel-stack">
                <Skeleton width={titleW} height={12} />
                <Skeleton height={14} />
                <Skeleton height={14} />
                <Skeleton width="70%" height={14} />
              </div>
            </Panel>
          ))}
        </div>
      </div>
    </div>
  )
}
