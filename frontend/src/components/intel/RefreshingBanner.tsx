import { ResearchingStatus, type RefreshStage } from './ResearchingStatus'

/**
 * Floating "updating in the background" banner, shown above the Ask Lucius bar while
 * STALE (cached) news/analysis is on screen and a refresh is in flight. Stale-while-
 * revalidate is otherwise invisible — a user could read last session's brief without
 * realising fresher data is on the way — so this surfaces the background refresh.
 *
 * The caption on top reflects the backend's REAL refresh_stage (see ResearchingStatus) —
 * driven by useAssetDetail's fast poll while refreshing, not a fixed cycling timer; a
 * single calm line below states plainly that the current view is the last saved one.
 */
export function RefreshingBanner({ stage }: { stage?: RefreshStage | null }) {
  return (
    <div
      className="asd-refreshing-banner"
      role="status"
      aria-label="Refreshing news and analysis in the background"
    >
      <span className="asd-refreshing-spinner" aria-hidden="true" />
      <div className="asd-refreshing-text">
        <ResearchingStatus stage={stage} />
        <span className="asd-refreshing-sub">Refreshing. Showing your last saved view</span>
      </div>
      <span className="asd-refreshing-bar" aria-hidden="true" />
    </div>
  )
}
