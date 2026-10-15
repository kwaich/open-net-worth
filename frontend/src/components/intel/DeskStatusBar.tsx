import type { ReactNode } from 'react'
import { Archive, Check } from '../../ui/icons'
import { fDateTime, timeAgo } from '../../utils/format'
import { QUEUE_META, QUEUE_ORDER, type QueueStatus } from './signalQueue'
import type { StoryArc } from './storyMeta'

/** "Mark all read" + the Archive toggle for one tab's stories — the ticker desks' status bar and
 *  the Topics & macro feed bar share it. */
export function FeedTools({
  unread,
  archiveCount,
  showArchive,
  onToggleArchive,
  onMarkAllRead,
  backLabel = 'Back',
  children,
}: {
  unread: StoryArc[]
  archiveCount: number
  showArchive: boolean
  onToggleArchive: () => void
  onMarkAllRead: (ids: string[]) => void
  backLabel?: string
  /** Shown before the two tools (a sort menu, the last-scan note). */
  children?: ReactNode
}) {
  return (
    <div className="intel-feed-tools">
      {children}
      {!showArchive && unread.length > 0 && (
        <button
          type="button"
          className="intel-feed-tool"
          onClick={() => onMarkAllRead(unread.map((s) => s.id))}
          title="Mark every story here read"
        >
          <Check size={13} />
          Mark all read
          <span className="intel-feed-tool-count intel-feed-tool-count--accent">
            {unread.length}
          </span>
        </button>
      )}
      <button
        type="button"
        className={`intel-feed-tool intel-feed-tool--archive${showArchive ? ' intel-feed-tool--active' : ''}`}
        onClick={onToggleArchive}
        disabled={!archiveCount && !showArchive}
        title="Dismissed and auto-resolved stories"
      >
        <Archive size={13} />
        {showArchive ? backLabel : 'View archived'}
        <span className="intel-feed-tool-count">{archiveCount}</span>
      </button>
    </div>
  )
}

function ScanNote({ at }: { at: string | null }) {
  return (
    <span
      className="intel-desk-scan"
      title={
        at
          ? `Last news scan: ${fDateTime(at)}`
          : 'No news scan has run yet — press Scan, or wait for the schedule.'
      }
    >
      {at ? `News scanned ${timeAgo(at)}` : 'News not scanned yet'}
    </span>
  )
}

/** A ticker desk's status bar: a jump to the first story of each status, then the tools. */
export function DeskStatusBar({
  counts,
  unread,
  archiveCount,
  showArchive,
  onToggleArchive,
  onMarkAllRead,
  onFocusStory,
  newsScannedAt,
  toolbar,
}: {
  counts: Record<QueueStatus, StoryArc[]>
  unread: StoryArc[]
  archiveCount: number
  showArchive: boolean
  onToggleArchive: () => void
  onMarkAllRead: (ids: string[]) => void
  onFocusStory: (id: string) => void
  newsScannedAt?: string | null
  toolbar?: ReactNode
}) {
  return (
    <div className="intel-desk-bar">
      <div className="intel-desk-status">
        {QUEUE_ORDER.map((k) =>
          counts[k].length ? (
            <button
              key={k}
              type="button"
              className="intel-desk-status-btn"
              onClick={() => onFocusStory(counts[k][0]!.id)}
              title={`Jump to the first "${QUEUE_META[k].label}" story`}
            >
              <span
                className="intel-desk-line-dot"
                style={{ background: QUEUE_META[k].color }}
                aria-hidden
              />
              {counts[k].length} {QUEUE_META[k].label.toLowerCase()}
            </button>
          ) : null,
        )}
        {counts.action_needed.length === 0 && (
          <span className="intel-desk-calm">
            <Check size={12} aria-hidden /> Nothing needs a decision
          </span>
        )}
      </div>
      <FeedTools
        unread={unread}
        archiveCount={archiveCount}
        showArchive={showArchive}
        onToggleArchive={onToggleArchive}
        onMarkAllRead={onMarkAllRead}
      >
        {toolbar}
        {newsScannedAt !== undefined && <ScanNote at={newsScannedAt} />}
      </FeedTools>
    </div>
  )
}
