// WatchlistTab — Investments › Watchlist: a broker-style quote board for each named watchlist.
// One tab per list (like a broker's Favorites / AI & Energy strip), cards or a table with
// owner-picked columns, and an optional 30-day sparkline. Read-only here; lists and their
// members are managed on Intel (or by asking Lucius).

import { useMemo, useState } from 'react'
import { Button, Cluster, TabSkeleton, EmptyState, ErrorState } from '../../ui'
import { LuciusCta } from '../../intro/LuciusCta'
import { ChartLine, Columns3, Eye } from '../../../ui/icons'
import { useNavigate } from '@tanstack/react-router'
import { useIsMobile } from '../../../hooks/useIsMobile'
import { requestIntelStoryFocus, useIntelStoryIndex } from '../../../hooks/useIntelStoryIndex'
import type { WatchQuoteRow } from '../../../types/watchlistQuotes'
import {
  rowsForList,
  sortWatchRows,
  useWatchlistBoard,
  type WatchSort,
  type WatchSortKey,
} from './useWatchlistBoard'
import { WatchCards, WatchTable } from './WatchBoard'
import { WatchColumnsModal } from './WatchColumnsModal'
import { columnsFor, loadWatchColumns, saveWatchColumns } from './watchColumns'

const LIST_KEY = 'inv-watch-list'
const DENSITY_KEY = 'inv-watch-density'
const SPARK_KEY = 'inv-watch-spark'
const SORT_KEY = 'inv-watch-sort'

function load(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}
function save(key: string, value: string): void {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* private mode — remembered for this session only */
  }
}
function loadSort(): WatchSort {
  try {
    const v = JSON.parse(load(SORT_KEY) || '')
    if (v && typeof v.key === 'string' && (v.dir === 1 || v.dir === -1)) return v
  } catch {
    /* fall through */
  }
  return { key: 'list', dir: 1 }
}

interface Props {
  onNavigate?: (tab: string) => void
  onAskLucius?: (text: string) => void
}

const WATCHLIST_MESSAGE =
  'Help me create a watchlist. Ask which companies, funds or coins I want to follow, then ' +
  'add them. Ask me one thing at a time.'

export function WatchlistTab({ onNavigate, onAskLucius }: Props) {
  const { board, loading, error, refresh } = useWatchlistBoard()
  // useNavigate, not useAssetNav(): Investments renders outside <AssetNavProvider>. Same
  // destination openAsset() picks for a ticker (the quote page resolves it once mounted).
  const navigate = useNavigate()
  const isMobile = useIsMobile()
  // Live Intel stories per ticker — the same cached read Holdings uses (no extra fetch).
  const storyIndex = useIntelStoryIndex()
  const openStory = onNavigate
    ? (storyId: string) => {
        requestIntelStoryFocus(storyId)
        onNavigate('intel')
      }
    : undefined
  const [listPick, setListPick] = useState<string | null>(() => load(LIST_KEY))
  const [search, setSearch] = useState('')
  const [density, setDensityState] = useState<'cards' | 'table'>(() =>
    load(DENSITY_KEY) === 'table' ? 'table' : 'cards',
  )
  const [showSpark, setShowSpark] = useState(() => load(SPARK_KEY) !== '0')
  const [sort, setSort] = useState<WatchSort>(loadSort)
  const [cols, setCols] = useState<string[]>(loadWatchColumns)
  const [colsOpen, setColsOpen] = useState(false)

  // The auto-synced "My holdings" list is left out: it is the Holdings tab again.
  const lists = useMemo(() => (board?.watchlists ?? []).filter((l) => !l.is_auto_sync), [board])
  const rows = useMemo(() => board?.rows ?? [], [board])
  // A remembered list that no longer exists (or is the holdings list) falls back to the first.
  const listId = lists.find((l) => l.id === listPick)?.id ?? lists[0]?.id ?? ''
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const r of rows) for (const id of r.watchlist_ids) c[id] = (c[id] ?? 0) + 1
    return c
  }, [rows])
  const visible = useMemo(
    () => sortWatchRows(rowsForList(rows, listId, search), sort),
    [rows, listId, search, sort],
  )

  const pickList = (id: string) => {
    setListPick(id)
    save(LIST_KEY, id)
  }
  const setDensity = (d: 'cards' | 'table') => {
    setDensityState(d)
    save(DENSITY_KEY, d)
  }
  const toggleSpark = () => {
    setShowSpark((v) => {
      save(SPARK_KEY, v ? '0' : '1')
      return !v
    })
  }
  // Click cycles: descending → ascending → back to the list's own order.
  const onSort = (key: WatchSortKey) => {
    setSort((prev) => {
      const next: WatchSort =
        prev.key !== key
          ? { key, dir: key === 'sym' ? 1 : -1 }
          : prev.dir === (key === 'sym' ? 1 : -1)
            ? { key, dir: -prev.dir }
            : { key: 'list', dir: 1 }
      save(SORT_KEY, JSON.stringify(next))
      return next
    })
  }
  const onCols = (keys: string[]) => {
    setCols(keys)
    saveWatchColumns(keys)
  }
  const open = (r: WatchQuoteRow) =>
    navigate({
      to: '/quote/$symbol',
      params: { symbol: r.ticker },
      search: { from: 'investments', label: r.name },
    })
  if (loading)
    return <TabSkeleton variant="holdings" label="Loading your watchlists…" header={false} />
  if (error)
    return (
      <ErrorState
        title="Couldn't load your watchlists"
        message="The quote board didn't answer. Check your connection and try again."
        onRetry={() => void refresh()}
      />
    )
  if (!lists.length)
    return (
      <EmptyState
        fill
        icon={<Eye size={28} strokeWidth={1.5} aria-hidden="true" />}
        title="No watchlists yet"
        body="Create a watchlist on Intel and its tickers show up here with live quotes."
        action={
          <Cluster gap="--space-3" justify="center">
            {onNavigate && (
              <Button variant="primary" size="sm" onClick={() => onNavigate('intel')}>
                Go to Intel
              </Button>
            )}
            <LuciusCta
              label="Create with Lucius"
              message={WATCHLIST_MESSAGE}
              onAskLucius={onAskLucius}
            />
          </Cluster>
        }
      />
    )

  return (
    <div className="mk-watch">
      {/* Search + list pills are ONE header: on a phone it pins below the sub-tabs as a unit. */}
      <div className="mk-feed-head">
        <div className="mk-toolbar">
          <label className="mk-search-wrap">
            <svg
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              aria-hidden="true"
            >
              <circle cx="7" cy="7" r="4.5" />
              <path d="m10.4 10.4 3.1 3.1" strokeLinecap="round" />
            </svg>
            <input
              className="mk-search"
              aria-label="Search watchlist"
              placeholder={isMobile ? 'Search…' : 'Search this watchlist…'}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
          <button
            type="button"
            className={`mk-wtool${showSpark ? ' on' : ''}`}
            aria-pressed={showSpark}
            title="Sparkline charts"
            onClick={toggleSpark}
          >
            <ChartLine size={15} strokeWidth={1.7} aria-hidden="true" />
            {!isMobile && <span>Sparklines</span>}
          </button>
          {density === 'table' && (
            <button
              type="button"
              className="mk-wtool"
              title="Manage columns"
              onClick={() => setColsOpen(true)}
            >
              <Columns3 size={15} strokeWidth={1.7} aria-hidden="true" />
              {!isMobile && <span>Columns</span>}
            </button>
          )}
          <div className="mk-density" role="radiogroup" aria-label="Watchlist density">
            <button
              type="button"
              role="radio"
              aria-checked={density === 'cards'}
              aria-label="Card view"
              title="Card view"
              className={density === 'cards' ? 'on' : ''}
              onClick={() => setDensity('cards')}
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                aria-hidden="true"
              >
                <rect x="2" y="2.5" width="12" height="4.5" rx="1.2" />
                <rect x="2" y="9" width="12" height="4.5" rx="1.2" />
              </svg>
            </button>
            <button
              type="button"
              role="radio"
              aria-checked={density === 'table'}
              aria-label="Table view"
              title="Table view"
              className={density === 'table' ? 'on' : ''}
              onClick={() => setDensity('table')}
            >
              <svg
                width="15"
                height="15"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                aria-hidden="true"
              >
                <line x1="2.5" y1="4" x2="13.5" y2="4" />
                <line x1="2.5" y1="8" x2="13.5" y2="8" />
                <line x1="2.5" y1="12" x2="13.5" y2="12" />
              </svg>
            </button>
          </div>
        </div>

        <div className="intel-feed-bar">
          <div className="mk-chips" role="tablist" aria-label="Watchlists">
            {lists.map((l) => (
              <button
                key={l.id}
                type="button"
                role="tab"
                aria-selected={l.id === listId}
                className={`mk-chip${l.id === listId ? ' is-on' : ''}`}
                onClick={() => pickList(l.id)}
              >
                {l.name}
                <span className="mk-chip-n">{counts[l.id] ?? 0}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {visible.length === 0 ? (
        <div className="mk-empty-filter">
          {search ? 'No tickers match your search.' : 'This watchlist has no tickers yet.'}
        </div>
      ) : density === 'cards' ? (
        <WatchCards
          rows={visible}
          sort={sort}
          onSort={onSort}
          showSpark={showSpark}
          onOpen={open}
          storyIndex={storyIndex}
          onOpenStory={openStory}
        />
      ) : (
        <WatchTable
          rows={visible}
          sort={sort}
          onSort={onSort}
          showSpark={showSpark}
          onOpen={open}
          storyIndex={storyIndex}
          onOpenStory={openStory}
          columns={columnsFor(cols)}
        />
      )}

      {/* Mounted only while open (like Holdings' dialogs): a closed dialog left in the tree
          looped on its presence state when the window crossed the phone breakpoint. */}
      {colsOpen && (
        <WatchColumnsModal open value={cols} onChange={onCols} onClose={() => setColsOpen(false)} />
      )}
    </div>
  )
}
