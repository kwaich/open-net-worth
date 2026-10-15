import { useMemo } from 'react'
import { FeedTools } from './DeskStatusBar'
import { StoryCards, type StoryHandlers } from './StoryCards'
import { QUEUE_META, QUEUE_ORDER, classifySignal } from './signalQueue'
import type { StoryArc } from './storyMeta'

// One Intel tab's feed: the type filter bar, "Mark all read" / Archive for THIS tab's stories,
// and the status-grouped story cards. The parent (Intel.tsx) decides which stories belong here.

export type FilterId = 'all' | 'risk' | 'price_alert' | 'opportunity' | 'macro' | 'topic' | 'asset'
const FILTERS: { id: FilterId; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'risk', label: 'Risk' },
  { id: 'price_alert', label: 'Alerts' },
  { id: 'opportunity', label: 'Opportunities' },
  { id: 'macro', label: 'Macro' },
  { id: 'topic', label: 'Topics' },
  { id: 'asset', label: 'Updates' },
]

interface Props extends StoryHandlers {
  feed: StoryArc[]
  archive: StoryArc[]
  filter: FilterId
  onFilter: (f: FilterId) => void
  showArchive: boolean
  onToggleArchive: () => void
  /** Shown when this tab's live feed is empty. */
  emptyText: string
  onMarkAllRead: (ids: string[]) => void
}

export function IntelFeedPanel(props: Props) {
  const { feed, archive, filter, onFilter, showArchive, onToggleArchive, emptyText } = props
  const visible = showArchive ? archive : feed
  const filtered = filter === 'all' ? visible : visible.filter((s) => s.type === filter)
  const unread = feed.filter((s) => !s.read)
  const counts = useMemo(() => {
    const c = Object.fromEntries(FILTERS.map((f) => [f.id, 0])) as Record<FilterId, number>
    c.all = visible.length
    for (const s of visible) if (s.type in c) c[s.type as FilterId] += 1
    return c
  }, [visible])
  // Only the types this tab actually has (plus the active one, so a filter never vanishes
  // out from under the user).
  const shownFilters = FILTERS.filter((f) => f.id === 'all' || counts[f.id] > 0 || filter === f.id)

  return (
    <>
      <div className="intel-feed-bar">
        <div className="intel-feed-filter" role="group" aria-label="Filter stories by type">
          {shownFilters.map((f) => (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              className={`intel-feed-filter-btn${filter === f.id ? ' intel-feed-filter-btn--active' : ''}`}
              onClick={() => onFilter(f.id)}
            >
              {f.label}
              <span className="intel-feed-filter-count">{counts[f.id]}</span>
            </button>
          ))}
        </div>
        <FeedTools
          unread={unread}
          archiveCount={archive.length}
          showArchive={showArchive}
          onToggleArchive={onToggleArchive}
          onMarkAllRead={props.onMarkAllRead}
          backLabel="Back to feed"
        />
      </div>

      {QUEUE_ORDER.map((status) => {
        const group = filtered.filter((s) => classifySignal(s) === status)
        if (group.length === 0) return null
        return (
          <section key={status} className="intel-sec">
            <div className="intel-sec-head">
              <span
                className="intel-sec-dot"
                style={{ background: QUEUE_META[status].color }}
                aria-hidden
              />
              <span className="intel-sec-title">{QUEUE_META[status].label}</span>
              <span className="intel-sec-count">{group.length}</span>
            </div>
            <StoryCards stories={group} h={props} />
          </section>
        )
      })}

      {filtered.length === 0 && (
        <div className="intel-feed-empty">
          {showArchive
            ? 'No archived stories on this tab.'
            : filter === 'all'
              ? emptyText
              : 'No stories of this type here yet.'}
        </div>
      )}
    </>
  )
}
