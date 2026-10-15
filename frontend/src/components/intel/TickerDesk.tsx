import { useId, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Skeleton } from '../ui'
import { PulseColHead, PulseRow } from './PulseRow'
import { DeskStatusBar } from './DeskStatusBar'
import { StoryCards, StoryLine, type StoryHandlers } from './StoryCards'
import type { StoryArc } from './storyMeta'
import type { TrendPeriod, WatchPulseRow } from '../../types/watchPulse'
import { usePulsePeriod } from '../../hooks/usePulsePeriod'
import { usePulseSort } from '../../hooks/usePulseSort'
import { PulseSortMenu } from './PulseSortMenu'
import { sortPulseRows } from './pulseHelp'
import { QUIET_RANK, byUrgency, groupByName, type NameGroup } from './tickerGroups'
import { QUEUE_ORDER, classifySignal, type QueueStatus } from './signalQueue'

// One Intel tab's body, organised by TICKER: each name's price, trend and flags (a pulse
// row) with that name's own stories folded underneath, most urgent first. Used by My holdings
// (inside the Holdings brief card) and each watchlist tab (inside the Watchlist pulse card), so
// the pulse and the news scan are one list — "needs attention", "active" and "monitoring" are an
// order and a status dot, not separate sections repeating the same stories.

const PORTFOLIO_KEY = '__portfolio'

interface Props extends StoryHandlers {
  rows: WatchPulseRow[]
  /** The benchmark the trend's dashed line shows (from the pulse). */
  benchSymbol?: string | null
  /** Prices haven't arrived yet — hold a placeholder rather than render rows twice. */
  loading: boolean
  /** Names in scope with no live price (counted in the footer). */
  unpriced: number
  noun: { one: string; many: string }
  /** Tag the names you own (a list tab, where owned and watched names mix). */
  markOwned?: boolean
  newsScannedAt?: string | null
  /** The remembered sort's storage key (`PULSE_SORT_KEYS`). */
  sortKey: string
  /** Shown when nothing is in scope at all. */
  empty: ReactNode
  feed: StoryArc[]
  archive: StoryArc[]
  showArchive: boolean
  onToggleArchive: () => void
  onMarkAllRead: (ids: string[]) => void
  onFocusStory: (id: string) => void
  onEditAlerts: (row: WatchPulseRow) => void
}

/** Which names have their stories unfolded. A jump to a story (status bar, another tab,
 *  Investments) unfolds its name once — adjusted during render — after which the row is the
 *  user's to collapse again. */
function useOpenGroups(active: string | null, activeKey: string | null) {
  const [openKeys, setOpenKeys] = useState<Set<string>>(new Set())
  const [seenActive, setSeenActive] = useState<string | null>(null)
  if (active !== seenActive) {
    setSeenActive(active)
    if (activeKey && !openKeys.has(activeKey)) setOpenKeys(new Set([...openKeys, activeKey]))
  }
  const toggle = (key: string) =>
    setOpenKeys((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  return { isOpen: (key: string) => openKeys.has(key), toggle }
}

function statusCounts(feed: StoryArc[]): Record<QueueStatus, StoryArc[]> {
  const c: Record<QueueStatus, StoryArc[]> = {
    action_needed: [],
    needs_recheck: [],
    active: [],
    monitoring: [],
  }
  for (const s of feed) (c[classifySignal(s)] ?? c.monitoring).push(s)
  for (const k of QUEUE_ORDER) c[k].sort(byUrgency)
  return c
}

export function TickerDesk(props: Props) {
  const { feed, archive, showArchive, loading, unpriced, noun } = props
  const [showQuiet, setShowQuiet] = useState(false)
  const [period, setPeriod] = usePulsePeriod()
  const [sort, setSort] = usePulseSort(props.sortKey)
  const portfolioHeadId = useId()
  const sorting = props.rows.length > 1
  const rows = useMemo(
    () => (sorting ? sortPulseRows(props.rows, sort, period) : props.rows),
    [sorting, props.rows, sort, period],
  )
  // "Most urgent" floats names with a decision to make; any other sort is the row order itself.
  const order = sort === 'attention' ? 'urgency' : 'given'
  const { groups, portfolio } = useMemo(
    () => groupByName(rows, feed, { order }),
    [rows, feed, order],
  )
  const activeKey = useMemo(() => {
    const s = feed.find((x) => x.id === props.active)
    if (!s) return null
    return (s.ticker ?? '').trim().toUpperCase() || PORTFOLIO_KEY
  }, [feed, props.active])
  const { isOpen, toggle } = useOpenGroups(props.active, activeKey)
  const counts = useMemo(() => statusCounts(feed), [feed])

  const busy = groups.filter((g) => g.rank < QUIET_RANK || (g.row?.findings.length ?? 0) > 0)
  const quiet = groups.filter((g) => !busy.includes(g))
  const nameRow = (g: NameGroup, open: boolean, onToggle: () => void) => (
    <NameRow key={g.key} g={g} open={open} onToggle={onToggle} p={props} period={period} />
  )

  return (
    <div className="intel-desk">
      <DeskStatusBar
        counts={counts}
        unread={feed.filter((s) => !s.read)}
        archiveCount={archive.length}
        showArchive={showArchive}
        onToggleArchive={props.onToggleArchive}
        onMarkAllRead={props.onMarkAllRead}
        onFocusStory={props.onFocusStory}
        newsScannedAt={props.newsScannedAt}
        toolbar={sorting ? <PulseSortMenu value={sort} onChange={setSort} /> : null}
      />

      {showArchive ? (
        <div className="intel-desk-archive">
          {archive.length ? (
            <StoryCards stories={[...archive].sort(byUrgency)} h={props} />
          ) : (
            <div className="intel-feed-empty">No archived stories here.</div>
          )}
        </div>
      ) : (
        <>
          {portfolio.length > 0 && (
            <section className="intel-desk-group" aria-labelledby={portfolioHeadId}>
              <div className="intel-desk-group-head" id={portfolioHeadId}>
                Across your portfolio
              </div>
              <StoryLine
                stories={portfolio}
                open={isOpen(PORTFOLIO_KEY)}
                onToggle={() => toggle(PORTFOLIO_KEY)}
              />
              {isOpen(PORTFOLIO_KEY) && (
                <div className="intel-desk-stories">
                  <StoryCards stories={portfolio} h={props} />
                </div>
              )}
            </section>
          )}

          {(rows.length > 0 || groups.length > 0 || loading) && (
            <PulseColHead period={period} onPeriod={setPeriod} />
          )}
          {/* Until prices land, hold a placeholder: rendering story-only rows first and swapping
              them for full rows a moment later made every row jump on a cold open. */}
          {loading ? (
            <div className="intel-brief-skel">
              <Skeleton height={44} />
              <Skeleton height={44} />
            </div>
          ) : (
            <>
              {busy.map((g) => nameRow(g, isOpen(g.key), () => toggle(g.key)))}
              {quiet.length > 0 && (
                <button
                  type="button"
                  className="intel-pulse-more"
                  aria-expanded={showQuiet}
                  onClick={() => setShowQuiet((v) => !v)}
                >
                  {showQuiet
                    ? `Hide quiet ${noun.many}`
                    : `${quiet.length} quiet ${quiet.length === 1 ? noun.one : noun.many} — no flags or stories`}
                </button>
              )}
              {showQuiet && quiet.map((g) => nameRow(g, false, () => undefined))}
            </>
          )}

          {!loading && groups.length === 0 && portfolio.length === 0 && props.empty}
          {!loading && unpriced > 0 && (
            <div className="intel-pulse-foot">
              {unpriced} {unpriced === 1 ? noun.one : noun.many} without a live price.
            </div>
          )}
        </>
      )}
    </div>
  )
}

function NameRow({
  g,
  open,
  onToggle,
  p,
  period,
}: {
  g: NameGroup
  open: boolean
  onToggle: () => void
  p: Props
  period: TrendPeriod
}) {
  const stories = g.stories.length ? (
    <>
      <StoryLine
        stories={g.stories}
        open={open}
        onToggle={onToggle}
        flagCount={g.row?.findings.length}
      />
      {open && (
        <div className="intel-desk-stories">
          <StoryCards stories={g.stories} h={p} />
        </div>
      )}
    </>
  ) : null
  if (g.row) {
    return (
      <PulseRow
        row={g.row}
        period={period}
        benchSymbol={p.benchSymbol}
        onEditAlerts={p.onEditAlerts}
        markOwned={p.markOwned}
        className="intel-desk-row"
      >
        {stories}
      </PulseRow>
    )
  }
  return (
    <div className="intel-pulse-row intel-desk-row">
      <div className="intel-pulse-sym">{g.key}</div>
      {stories}
    </div>
  )
}
