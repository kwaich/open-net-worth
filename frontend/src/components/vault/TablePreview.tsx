import { useEffect, useRef, useState, type UIEvent } from 'react'
import { fetchVaultTable, type VaultItem, type VaultTable } from '../../api/vault'
import { NoPreview } from './NoPreview'

function columnLetter(index: number): string {
  let n = index + 1
  let out = ''
  while (n > 0) {
    const r = (n - 1) % 26
    out = String.fromCharCode(65 + r) + out
    n = Math.floor((n - 1) / 26)
  }
  return out
}

type Grid = {
  key: string
  sheets: string[]
  sheet: string
  rows: string[][]
  columns: number
  hasMore: boolean
  total: number | null
  moreColumns: boolean
}
type Loaded = Grid | { key: string; error: string }

const fromPage = (key: string, t: VaultTable): Grid => ({
  key,
  sheets: t.sheets,
  sheet: t.sheet,
  rows: t.rows,
  columns: t.columns,
  hasMore: t.has_more,
  total: t.total_rows,
  moreColumns: t.more_columns,
})
const LOAD_AHEAD_PX = 240

function note(grid: Grid, more: 'idle' | 'loading' | 'failed'): string | null {
  const shown = grid.rows.length.toLocaleString()
  const of = grid.total ? ` of ${grid.total.toLocaleString()}` : ''
  if (more === 'failed') return 'Couldn’t load more rows — scroll to try again.'
  if (grid.hasMore)
    return `Rows 1–${shown}${of}${more === 'loading' ? ' — loading more…' : ' — scroll for more'}`
  if (grid.moreColumns)
    return `Showing the first ${grid.columns} columns — download it for the rest.`
  return null
}

/** A CSV or Excel file's rows, as the file has them — a small spreadsheet with column letters
 *  and row numbers, one sheet at a time. Scrolls inside its frame and loads the next page of
 *  rows as you near the bottom (owner: "why we show only 200?"). */
export function TablePreview({ item }: { item: VaultItem }) {
  const [sheet, setSheet] = useState<string | undefined>(undefined)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [more, setMore] = useState<'idle' | 'loading' | 'failed'>('idle')
  // Synchronous, unlike `more`: two scroll events in one tick must not fetch a page twice.
  const inFlight = useRef(false)
  const key = `${item.id}:${sheet ?? ''}`
  useEffect(() => {
    let alive = true
    fetchVaultTable(item.id, sheet).then(
      (table) => alive && setLoaded(fromPage(key, table)),
      (e) => alive && setLoaded({ key, error: e instanceof Error ? e.message : String(e) }),
    )
    return () => {
      alive = false
    }
  }, [item.id, sheet, key])
  const current = loaded?.key === key ? loaded : null
  if (!current) return <div className="vault-table-frame vault-table-frame--loading" />
  if ('error' in current) return <NoPreview item={item} reason={current.error} />
  const grid = current

  const loadMore = () => {
    if (!grid.hasMore || inFlight.current) return
    inFlight.current = true
    setMore('loading')
    fetchVaultTable(item.id, sheet, grid.rows.length).then(
      (page) => {
        inFlight.current = false
        // Only the page that starts where the loaded rows end — never one twice.
        setLoaded((prev) =>
          prev && prev.key === key && !('error' in prev) && prev.rows.length === page.offset
            ? {
                ...prev,
                rows: [...prev.rows, ...page.rows],
                columns: Math.max(prev.columns, page.columns),
                hasMore: page.has_more,
                total: page.total_rows ?? prev.total,
                moreColumns: prev.moreColumns || page.more_columns,
              }
            : prev,
        )
        setMore('idle')
      },
      () => {
        inFlight.current = false
        setMore('failed')
      },
    )
  }
  const onScroll = (e: UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - LOAD_AHEAD_PX) loadMore()
  }
  const cols = Array.from({ length: grid.columns }, (_, i) => i)
  const status = note(grid, more)
  return (
    <div className="vault-table">
      <div
        className="vault-table-frame"
        role="region"
        aria-label={`${item.title}${grid.sheet ? ` — ${grid.sheet}` : ''}`}
        tabIndex={0}
        onScroll={onScroll}
      >
        <table className="vault-grid">
          <thead>
            <tr>
              <th aria-hidden="true" />
              {cols.map((c) => (
                <th key={c} scope="col">
                  {columnLetter(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {grid.rows.map((row, r) => (
              <tr key={r}>
                <th scope="row">{r + 1}</th>
                {cols.map((c) => (
                  <td key={c} title={row[c] || undefined}>
                    {row[c] ?? ''}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {grid.sheets.length > 1 && (
        <div className="vault-sheet-tabs" role="group" aria-label="Sheets">
          {grid.sheets.map((name) => (
            <button
              key={name}
              type="button"
              className="vault-sheet-tab"
              aria-pressed={name === grid.sheet}
              onClick={() => {
                inFlight.current = false
                setMore('idle')
                setSheet(name)
              }}
            >
              {name}
            </button>
          ))}
        </div>
      )}
      {status && <p className="vault-table-note">{status}</p>}
    </div>
  )
}
