import { ChevronRight } from '../../ui/icons'
import { timeAgo } from '../../utils/format'
import { StoryArcCard } from './StoryArcCard'
import { signalMeta, storyHeadline } from './storyMeta'
import { QUEUE_META, classifySignal } from './signalQueue'
import type { StoryArc } from './storyMeta'

/** What a story card can do — shared by the ticker desks and the Topics & macro feed. */
export interface StoryHandlers {
  active: string | null
  onRead: (id: string) => void
  onDismiss: (id: string) => void
  onRestore: (id: string) => void
  onDelete: (id: string) => void
  onAskLucius: (text: string) => void
  registerRef: (id: string, el: HTMLElement | null) => void
}

export function StoryCards({ stories, h }: { stories: StoryArc[]; h: StoryHandlers }) {
  return (
    <>
      {stories.map((s) => (
        <StoryArcCard
          key={s.id}
          story={s}
          active={h.active === s.id}
          updatedLabel={timeAgo(s.updated_at)}
          read={!!s.read}
          dismissed={!!s.dismissed}
          resolved={!!s.resolved}
          resolvedReason={s.resolved_reason}
          onRead={h.onRead}
          onDismiss={h.onDismiss}
          onRestore={h.onRestore}
          onDelete={h.onDelete}
          onAskLucius={h.onAskLucius}
          registerRef={h.registerRef}
        />
      ))}
    </>
  )
}

// The collapsed face of a name's stories. The status is the name's most urgent one; the headline
// is its newest NEWS story — a price-signal story (`watch`) only restates the flags already on the
// row, so when that is all there is the line says "Price signal · N flags" instead of repeating
// them. Tapping it unfolds every story for the name in place.
export function StoryLine({
  stories,
  open,
  onToggle,
  flagCount,
}: {
  stories: StoryArc[]
  open: boolean
  onToggle: () => void
  flagCount?: number
}) {
  const top = stories[0]!
  const st = QUEUE_META[classifySignal(top)]
  const explained = stories.find((s) => s.explained)?.explained
  const hasRead = !explained && stories.some((s) => s.lucius_read)
  const lead = stories.find((s) => !s.watch) ?? null
  const unread = stories.filter((s) => !s.read).length
  const more = stories.length - 1
  const flags = flagCount ?? top.watch_kinds?.length ?? 0
  const title = explained
    ? explained.what_happened
    : lead
      ? storyHeadline(lead)
      : flags > 0
        ? `${flags} flag${flags === 1 ? '' : 's'}`
        : storyHeadline(top)
  return (
    <button
      type="button"
      className={`intel-desk-line${open ? ' intel-desk-line--open' : ''}`}
      aria-expanded={open}
      onClick={onToggle}
    >
      <span className="intel-desk-line-dot" style={{ background: st.color }} aria-hidden />
      <span className="intel-desk-line-status" style={{ color: st.color }}>
        {st.label}
      </span>
      <span
        className={`intel-desk-line-type${
          explained
            ? ' intel-desk-line-type--explained'
            : hasRead && !lead
              ? ' intel-desk-line-type--read'
              : ''
        }`}
      >
        {explained
          ? 'Explained'
          : lead
            ? signalMeta(lead.type).label
            : hasRead
              ? 'Lucius’s read'
              : 'Price signal'}
      </span>
      <span className="intel-desk-line-title">{title}</span>
      {more > 0 && <span className="intel-desk-line-more">+{more} more</span>}
      {unread > 0 && (
        <span className="intel-desk-line-unread" aria-label={`${unread} unread`}>
          {unread}
        </span>
      )}
      <ChevronRight size={12} className="intel-desk-line-chev" aria-hidden />
    </button>
  )
}
