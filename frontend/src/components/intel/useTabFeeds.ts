import { useMemo } from 'react'
import { buildTabs, listTickers, partitionByTab } from './intelTabs'
import type { StoryArc } from './storyMeta'
import type { Watchlist, WatchlistItem } from './WatchlistsSection'

export const isArchived = (s: StoryArc) => !!(s.dismissed || s.resolved || s.archived)

/** Intel's tabs (My holdings · each watchlist · Topics & macro) with their unread badges, and
 *  every story — live and archived — partitioned onto the tabs that claim it (`intelTabs`). */
export function useTabFeeds(
  stories: StoryArc[],
  items: WatchlistItem[],
  watchlists: Watchlist[],
  heldSet: Set<string>,
) {
  const tabDefs = useMemo(() => buildTabs(watchlists), [watchlists])
  const tickersByList = useMemo(() => listTickers(watchlists, items), [watchlists, items])
  // Tickers you watch without holding — owned names are never "watching" (intelScope).
  const watchedSet = useMemo(
    () =>
      new Set(
        items
          .filter((i) => i.type === 'ticker')
          .map((i) => i.value.toUpperCase())
          .filter((t) => !heldSet.has(t)),
      ),
    [items, heldSet],
  )
  const feedStories = useMemo(() => stories.filter((s) => !isArchived(s)), [stories])
  const feedByTab = useMemo(
    () => partitionByTab(feedStories, heldSet, watchedSet, tickersByList),
    [feedStories, heldSet, watchedSet, tickersByList],
  )
  const archiveByTab = useMemo(
    () => partitionByTab(stories.filter(isArchived), heldSet, watchedSet, tickersByList),
    [stories, heldSet, watchedSet, tickersByList],
  )
  const tabs = tabDefs.map((t) => {
    const unread = (feedByTab[t.key] ?? []).filter((s) => !s.read).length
    return { key: t.key, label: t.label, badge: unread || undefined }
  })
  return { tabDefs, tabs, tickersByList, feedStories, feedByTab, archiveByTab }
}
