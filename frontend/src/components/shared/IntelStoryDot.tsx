import type { TickerStories } from '../../hooks/useIntelStoryIndex'

// A small "N stories" marker on an Investments holding that has live Intel stories. It lives
// inside a clickable row, so it stops the click from also opening the asset page.
export function IntelStoryDot({
  stories,
  onOpen,
}: {
  stories: TickerStories | undefined
  onOpen: (storyId: string) => void
}) {
  if (!stories) return null
  const label = `${stories.count} live Intel ${stories.count === 1 ? 'story' : 'stories'}${stories.unread ? `, ${stories.unread} unread` : ''} — open in Intel`
  return (
    <button
      type="button"
      className={`intel-story-dot${stories.unread ? ' intel-story-dot--unread' : ''}`}
      title={label}
      aria-label={label}
      onClick={(e) => {
        e.stopPropagation()
        onOpen(stories.newestId)
      }}
    >
      {stories.count}
    </button>
  )
}
