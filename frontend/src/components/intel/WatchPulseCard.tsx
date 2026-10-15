import type { ReactNode } from 'react'
import { useWatchPulse } from '../../hooks/useWatchPulse'
import { usePulsePeriod } from '../../hooks/usePulsePeriod'
import { useWatchBrief } from '../../hooks/useWatchBrief'
import { Button, Cluster, EmptyState, Skeleton } from '../ui'
import { LuciusCta } from '../intro/LuciusCta'
import { Eye } from '../../ui/icons'
import { IntelCardHead } from './IntelCardHead'
import { OpinionLine } from './OpinionLine'
import { watchBrief } from './watchBrief'
import type { BriefLevel } from './watchBrief'
import type { WatchPulseRow } from '../../types/watchPulse'

// A watchlist tab's card: the Watchlist pulse header and its one-line brief, then the list's
// tickers — watched ones from the watched pulse, owned ones from the held pulse — handed to
// `renderBody` (a TickerDesk that sorts them and folds each ticker's stories under its row).
// Every figure is computed server-side by the same rules that raise the feed's stories.

const ASK_WATCHLIST =
  'Walk me through my watchlist today — what moved and why, what’s ahead or behind its sector, and what’s coming up.'

interface Props {
  /** The watchlist this tab is. `tickerCount` = every ticker on it, owned ones included, so
   *  unpriced ones count. */
  scope: { id: string; name: string; tickerCount: number }
  /** The list's tickers you own — rows from the held pulse, merged in so the tab mirrors the
   *  list exactly (they carry their position, and compare with your benchmark). */
  ownedRows: WatchPulseRow[]
  /** The held pulse is still loading: its rows aren't in yet, so nothing can be counted. */
  ownedLoading?: boolean
  /** Where "Add a ticker" goes (the Manage watchlists workspace). */
  onAddToWatchlist?: () => void
  /** Ask Lucius about the list (the header's button). */
  onChatSuggest?: (text: string) => void
  /** The list's rows and how many of its tickers have no live price. It still renders when
   *  prices are unavailable, so the news never disappears with the quote feed. */
  renderBody: (rows: WatchPulseRow[], unpriced: number) => ReactNode
  /** How much the brief says (Settings › Intelligence); 5 = Lucius's narrative, shown as the
   *  level-4 template while it loads or if it can't be written. */
  verbosity?: BriefLevel
}

function byAttention(a: WatchPulseRow, b: WatchPulseRow): number {
  return b.score - a.score || Math.abs(b.day_pct ?? 0) - Math.abs(a.day_pct ?? 0)
}

export function WatchPulseCard({
  scope,
  ownedRows,
  ownedLoading = false,
  onAddToWatchlist,
  onChatSuggest,
  renderBody,
  verbosity = 3,
}: Props) {
  const { data, loading, error, refetch } = useWatchPulse('watched')
  const [period] = usePulsePeriod()
  const narrated = useWatchBrief(scope.id, period, verbosity === 5)

  const head = (updatedAt?: string) => (
    <IntelCardHead
      icon={<Eye size={13} />}
      title="Watchlist pulse"
      ask={
        onChatSuggest && {
          label: 'Ask Lucius about this watchlist',
          onAsk: () => onChatSuggest(ASK_WATCHLIST),
        }
      }
      updatedAt={updatedAt}
    />
  )

  if (loading || ownedLoading) {
    return (
      <section className="intel-brief-card intel-pulse-card" aria-busy="true">
        {head()}
        <div className="intel-brief-skel">
          <Skeleton height={44} />
          <Skeleton height={44} />
        </div>
      </section>
    )
  }
  if (scope.tickerCount === 0) {
    return (
      <section className="intel-brief-card intel-pulse-card">
        {head()}
        <EmptyState
          icon={<Eye size={28} strokeWidth={1.5} aria-hidden="true" />}
          title={`“${scope.name}” has no tickers yet`}
          body="Add one to see its trend, moves and news here."
          action={
            <Cluster gap="--space-3" justify="center">
              {onAddToWatchlist && (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={onAddToWatchlist}
                  data-tour="setup-intel"
                >
                  Add a ticker
                </Button>
              )}
              <LuciusCta
                label="Add with Lucius"
                message={`Help me add tickers to my “${scope.name}” watchlist. Ask which companies, funds or coins I want to follow.`}
                onAskLucius={onChatSuggest}
              />
            </Cluster>
          }
        />
        {renderBody([], 0)}
      </section>
    )
  }
  if (error || !data || data.status === 'unavailable') {
    return (
      <section className="intel-brief-card intel-pulse-card">
        {head()}
        <div className="intel-pulse-note">
          Prices are unavailable right now — the news below is still current.{' '}
          <button type="button" className="intel-pulse-story" onClick={refetch}>
            Retry
          </button>
        </div>
        {renderBody(ownedRows, scope.tickerCount - ownedRows.length)}
      </section>
    )
  }

  // The watched pulse may be empty — every name on the list owned — and the card still shows.
  const rows = [...data.rows.filter((r) => r.watchlist_ids.includes(scope.id)), ...ownedRows]
  rows.sort(byAttention)
  const template = watchBrief(
    rows,
    period,
    data.benchmark?.symbol,
    scope.name,
    verbosity === 5 ? 4 : verbosity,
  )
  const narration = verbosity === 5 ? narrated.data?.narration?.trim() : null
  const brief = narration || template
  return (
    <section className="intel-brief-card intel-pulse-card">
      {head(data.generated_at)}
      {brief && (
        <div className="intel-brief-body">
          <p className="intel-brief-narrative">{brief}</p>
          {narration && <OpinionLine opinion={narrated.data} />}
        </div>
      )}
      {renderBody(rows, scope.tickerCount - rows.length)}
    </section>
  )
}
