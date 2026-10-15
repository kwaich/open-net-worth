import { useEffect, useState } from 'react'
import { authFetch } from '../../api/client'
import { ChevronRight, Plus } from '../../ui/icons'
import { Button, Modal, TextField } from '../ui'
import { fPrice, getCurrencySymbol, getBaseCurrency } from '../../utils/format'
import { NewsSourcesPanel, NEWS_SOURCES_SUBTITLE } from './NewsSourcesPanel'
import { LuciusReviewButton, LuciusReviewPanel } from './LuciusAssist'
import { ConfirmDialog } from './ConfirmDialog'
import type { ConfirmSpec } from './ConfirmDialog'
import { errorDetail } from './WatchlistsSection'
import { alertChipText } from './priceAlerts/alertModel'
import { isTriggeredNow } from './alertReview'
import type { Watchlist, WatchlistItem, PriceAlert } from './WatchlistsSection'
import type { TickerMeta } from './StoryArcCard'
import type { LuciusContext, LuciusFinding, LuciusReviewItem } from '../../utils/luciusEngine'
import { EntityRow } from '../../lucius/EntityRow'
import { entityRefProps } from '../../utils/mentionable'
import { buildCtxFields } from '../../lucius/ctxFields'
import { filterByDescriptor } from '../../lucius/entityRegistry'

const OP_SYM: Record<string, string> = { '>=': '≥', '<=': '≤', '>': '>', '<': '<' }
const cleanTicker = (t: string) => t.replace(/\.(DE|F|PA|SI|L|T|AX)$/, '')
const ccySym = (t: string | undefined, m: Record<string, string>) =>
  getCurrencySymbol((t && m[t]) || getBaseCurrency())

// ── Detailed rows ────────────────────────────────────────────────────────────

function DetailedAssetRow({
  item,
  alerts: alertsProp,
  watchlistChips,
  tickerMeta,
  tickerCcy,
  owned,
  onEdit,
  flat,
}: {
  item: WatchlistItem
  alerts?: PriceAlert[]
  watchlistChips?: Watchlist[]
  tickerMeta: Record<string, TickerMeta>
  tickerCcy: Record<string, string>
  owned: boolean
  onEdit: (i: WatchlistItem) => void
  /** Renders the ticker header without list-item chrome (no card wrapper, no divider) —
   * used where each row stands alone (e.g. one ticker per group in the alerts overview),
   * as opposed to several rows sharing one boxed list (Tickers/Topics tabs). */
  flat?: boolean
}) {
  const meta = tickerMeta[item.value]
  const sym = ccySym(item.value, tickerCcy)
  const alerts = alertsProp || []
  const muted = item.monitor_news === false
  return (
    <button
      type="button"
      className={`intel-mm-row${flat ? ' intel-mm-row--flat' : ''}`}
      onClick={() => onEdit(item)}
    >
      <div className="intel-mm-row-top">
        <span className="intel-mm-row-sym">{cleanTicker(item.value)}</span>
        <span className="intel-mm-row-name">{meta?.name || item.label || item.value}</span>
        {owned && <span className="intel-wlc-row-badge intel-wlc-row-badge--owned">OWNED</span>}
        {muted && <span className="intel-wlc-row-badge intel-wlc-row-badge--muted">MUTED</span>}
        {watchlistChips && watchlistChips.length > 0
          ? watchlistChips.map((w) => (
              <span key={w.id} className="intel-mm-wl-chip">
                {w.name}
              </span>
            ))
          : watchlistChips !== undefined && (
              <span className="intel-mm-wl-chip intel-mm-wl-chip--none">not in any watchlist</span>
            )}
        <span className="intel-mm-row-price money">
          {meta?.price != null ? `${sym}${fPrice(meta.price)}` : '—'}
        </span>
        <ChevronRight size={11} className="intel-wlc-row-chevron" />
      </div>
      {alerts.length > 0 ? (
        <div className="intel-mm-row-alerts">
          {/* `alert` is a registered mention type (descriptors/new_types.py, order=18) keyed on
              the stored alert id. The id is optional on the client type, so an alert that
              somehow arrives without one renders exactly as before rather than emitting an
              unresolvable ref. */}
          {alerts.map((a, i) => (
            <div
              key={i}
              className="intel-mm-alert"
              {...(a.id
                ? {
                    ...entityRefProps(
                      'alert',
                      a.id,
                      a.ticker ?? item.value,
                      `${a.ticker ?? item.value} alert ${a.op} ${a.value} ${a.note ?? ''}`,
                      `${OP_SYM[a.op] ?? a.op} ${a.value}`,
                    ),
                    'data-ctx': '',
                    'data-ctx-type': 'alert',
                    'data-ctx-fields': buildCtxFields(
                      filterByDescriptor('alert', {
                        ticker: a.ticker ?? item.value,
                        op: a.op,
                        value: a.value,
                        label: a.label,
                        note: a.note,
                      }),
                    ),
                    draggable: true,
                  }
                : {})}
            >
              <span className="intel-wlc-alert-chip">{alertChipText(a, sym)}</span>
              <span className="intel-mm-alert-note">{a.name || a.note || 'No instructions'}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="intel-mm-row-noalert">No alerts • tap to add</div>
      )}
    </button>
  )
}

function DetailedTopicRow({
  item,
  onEdit,
}: {
  item: WatchlistItem
  onEdit: (i: WatchlistItem) => void
}) {
  const muted = item.monitor_news === false
  return (
    <button type="button" className="intel-mm-row" onClick={() => onEdit(item)}>
      <div className="intel-mm-row-top">
        <span className="intel-wlc-topic-tag">topic</span>
        <span className="intel-mm-row-name intel-mm-topic-name">{item.label || item.value}</span>
        {item.added_by === 'ai' && (
          <span className="intel-wlc-row-badge intel-wlc-row-badge--ai">AI</span>
        )}
        {muted && <span className="intel-wlc-row-badge intel-wlc-row-badge--muted">MUTED</span>}
        <ChevronRight size={11} className="intel-wlc-row-chevron" />
      </div>
      <div className="intel-mm-topic-note">{item.note || 'No instructions yet.'}</div>
    </button>
  )
}

function AddTickerInput({ listId, onAdded }: { listId: string; onAdded: () => void }) {
  const [val, setVal] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  async function go() {
    const t = val.trim().toUpperCase()
    if (!t) return
    setBusy(true)
    setErr(null)
    try {
      const res = await authFetch('/api/watchlist/items', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'ticker',
          value: t,
          asset_class: 'equity',
          watchlist: listId,
        }),
      })
      if (res.status === 409) {
        setErr(`${t} is already in this watchlist.`)
        return
      }
      if (!res.ok) {
        setErr(await errorDetail(res, 'Could not add — try again.'))
        return
      }
      setVal('')
      onAdded()
    } catch {
      setErr('Could not add — try again.')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="intel-mm-add-block">
      <div className="intel-mm-add">
        <TextField
          value={val}
          onChange={(e) => {
            setVal(e.target.value.toUpperCase())
            if (err) setErr(null)
          }}
          onKeyDown={(e) => e.key === 'Enter' && void go()}
          placeholder="Add ticker — e.g. AAPL"
        />
        <Button
          variant="primary"
          size="sm"
          onClick={() => void go()}
          disabled={!val.trim() || busy}
        >
          {busy ? 'Adding…' : 'Add'}
        </Button>
      </div>
      {err && <div className="intel-wlc-add-err">{err}</div>}
    </div>
  )
}

// ── All-monitors view ────────────────────────────────────────────────────────

function AllMonitorsView({
  watchlists,
  items,
  alerts,
  tickerMeta,
  tickerCcy,
  ownedTickers,
  onEditAlerts,
  onEditTopic,
}: {
  watchlists: Watchlist[]
  items: WatchlistItem[]
  alerts: PriceAlert[]
  tickerMeta: Record<string, TickerMeta>
  tickerCcy: Record<string, string>
  ownedTickers: Set<string>
  onEditAlerts: (i: WatchlistItem, sourceListId?: string) => void
  onEditTopic: (i: WatchlistItem, sourceListId?: string) => void
}) {
  const [tab, setTab] = useState<'alerts' | 'topics'>('alerts')
  const [query, setQuery] = useState('')
  const [alertView, setAlertView] = useState<'all' | 'triggered' | 'disabled'>('all')
  const totalAlerts = alerts.length
  const totalTopics = items.filter((i) => i.type === 'topic' || i.type === 'company').length
  const triggeredCount = alerts.filter((a) =>
    isTriggeredNow(a, tickerMeta[a.ticker || '']?.price),
  ).length
  const disabledCount = alerts.filter((a) => a.enabled === false).length
  const match = (value: string) => value.toLowerCase().includes(query.trim().toLowerCase())
  return (
    <div className="intel-mm-pane">
      <div className="intel-mm-subtabs">
        <button
          type="button"
          className={`intel-mm-subtab${tab === 'alerts' ? ' intel-mm-subtab--active' : ''}`}
          onClick={() => setTab('alerts')}
        >
          Price alerts ({totalAlerts})
        </button>
        <button
          type="button"
          className={`intel-mm-subtab${tab === 'topics' ? ' intel-mm-subtab--active' : ''}`}
          onClick={() => setTab('topics')}
        >
          Topics ({totalTopics})
        </button>
      </div>
      <div className="intel-mm-find">
        <TextField
          aria-label="Find a monitor"
          placeholder="Find a ticker or monitor"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {tab === 'alerts' && (
        <div className="intel-mm-review-filters" role="group" aria-label="Filter price alerts">
          {(
            [
              ['all', `All ${totalAlerts}`],
              ['triggered', `Triggered now ${triggeredCount}`],
              ['disabled', `Disabled ${disabledCount}`],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              className={`intel-mm-review-filter${alertView === id ? ' intel-mm-review-filter--active' : ''}`}
              aria-pressed={alertView === id}
              onClick={() => setAlertView(id)}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {tab === 'alerts' &&
        (() => {
          // Group alerts by ticker
          const byTicker = new Map<string, PriceAlert[]>()
          for (const a of alerts) {
            const t = a.ticker || ''
            if (!t) continue
            if (alertView === 'triggered' && !isTriggeredNow(a, tickerMeta[t]?.price)) continue
            if (alertView === 'disabled' && a.enabled !== false) continue
            const item = items.find((i) => i.type === 'ticker' && i.value === t)
            const names = watchlists
              .filter((w) => item?.watchlist_ids?.includes(w.id))
              .map((w) => w.name)
            if (
              query.trim() &&
              ![
                t,
                item?.label || '',
                tickerMeta[t]?.name || '',
                a.name || '',
                a.note || '',
                ...names,
              ].some(match)
            )
              continue
            if (!byTicker.has(t)) byTicker.set(t, [])
            byTicker.get(t)!.push(a)
          }
          if (byTicker.size === 0)
            return (
              <div className="intel-mm-empty">
                {alertView === 'triggered'
                  ? 'No price alerts currently at their trigger level.'
                  : query
                    ? 'No monitors match your search.'
                    : alertView === 'disabled'
                      ? 'No disabled alerts.'
                      : 'No price alerts set yet.'}
              </div>
            )
          return Array.from(byTicker.entries())
            .sort(([a], [b]) => {
              const aTriggered = byTicker
                .get(a)
                ?.some((alert) => isTriggeredNow(alert, tickerMeta[a]?.price))
                ? 1
                : 0
              const bTriggered = byTicker
                .get(b)
                ?.some((alert) => isTriggeredNow(alert, tickerMeta[b]?.price))
                ? 1
                : 0
              return bTriggered - aTriggered || a.localeCompare(b)
            })
            .map(([ticker, tickerAlerts]) => {
              const item = items.find((i) => i.type === 'ticker' && i.value === ticker)
              const memberLists = watchlists.filter((w) => item?.watchlist_ids?.includes(w.id))
              // A standalone alert (ticker on no watchlist) has no item — synthesize one (id: '') so the
              // row renders AND opens the modal; PriceAlertsModal manages alerts by ticker and hides the
              // mute toggle when item.id is empty.
              const rowItem: WatchlistItem = item ?? {
                id: '',
                type: 'ticker',
                value: ticker,
                label: tickerMeta[ticker]?.name || ticker,
                asset_class: tickerAlerts[0]?.asset_class || 'equity',
                monitor_news: true,
                added_by: 'user',
                note: '',
                watchlist_ids: [],
                custom_feed_urls: [],
              }
              return (
                <div key={ticker} className="intel-mm-section">
                  <DetailedAssetRow
                    item={rowItem}
                    alerts={tickerAlerts}
                    watchlistChips={memberLists}
                    tickerMeta={tickerMeta}
                    tickerCcy={tickerCcy}
                    owned={ownedTickers.has(ticker)}
                    onEdit={() => onEditAlerts(rowItem)}
                    flat
                  />
                </div>
              )
            })
        })()}
      {/* Topics are followed on their own, in no watchlist (ADR 2026-09-27): one flat list,
          narrowed by the search box. */}
      {tab === 'topics' && totalTopics > 0 && (
        <div className="intel-mm-section">
          <div className="intel-mm-section-card">
            {items
              .filter(
                (i) =>
                  (i.type === 'topic' || i.type === 'company') &&
                  (!query.trim() || [i.label, i.value, i.note].some(match)),
              )
              .map((it) => (
                <DetailedTopicRow key={it.id} item={it} onEdit={(i) => onEditTopic(i)} />
              ))}
          </div>
        </div>
      )}
      {tab === 'topics' && totalTopics === 0 && (
        <div className="intel-mm-empty">No topics yet.</div>
      )}
    </div>
  )
}

// ── List-detail view ─────────────────────────────────────────────────────────

function ListDetailView({
  list,
  items,
  alerts,
  tickerMeta,
  tickerCcy,
  ownedTickers,
  luciusCtx,
  onEditAlerts,
  onRename,
  onToggleMute,
  onDelete,
  onRefresh,
}: {
  list: Watchlist
  items: WatchlistItem[]
  alerts: PriceAlert[]
  tickerMeta: Record<string, TickerMeta>
  tickerCcy: Record<string, string>
  ownedTickers: Set<string>
  luciusCtx: LuciusContext
  onEditAlerts: (i: WatchlistItem, sourceListId?: string) => void
  onRename: (id: string, name: string) => Promise<void>
  onToggleMute: (list: Watchlist) => void
  onDelete: (list: Watchlist) => void
  onRefresh: () => void
}) {
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(list.name)
  const [renameErr, setRenameErr] = useState<string | null>(null)
  const [reviewOpen, setReviewOpen] = useState(false)
  useEffect(() => {
    setName(list.name)
    setRenaming(false)
    setRenameErr(null)
    setReviewOpen(false)
  }, [list.id, list.name])

  async function commitRename() {
    const next = name.trim() || list.name
    if (next === list.name) {
      setRenaming(false)
      setRenameErr(null)
      return
    }
    try {
      await onRename(list.id, next)
      setRenameErr(null)
      setRenaming(false)
    } catch (e) {
      setRenameErr(e instanceof Error ? e.message : 'Could not rename — try again.')
      setName(list.name)
    }
  }

  // A watchlist holds tickers only — topics are followed in no list (ADR 2026-09-27).
  const listItems = items.filter(
    (i) => i.type === 'ticker' && (i.watchlist_ids ?? []).includes(list.id),
  )
  const assets = listItems
  const system = list.is_auto_sync
  const reviewItems: LuciusReviewItem[] = listItems.map((it) => ({
    id: it.id,
    kind: 'ticker',
    symbol: it.value,
    label: it.label,
    note: it.note,
    muted: it.monitor_news === false,
    alerts: alerts.filter((a) => (a.ticker || '') === it.value),
  }))

  function onFix(f: LuciusFinding) {
    const item = listItems.find((i) => i.id === f.itemId)
    if (item) onEditAlerts(item, list.id)
  }

  return (
    <div className="intel-mm-pane">
      <div className="intel-mm-list-head">
        {renaming && !system ? (
          <input
            autoFocus
            className="ui-input intel-mm-rename"
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              if (renameErr) setRenameErr(null)
            }}
            onBlur={() => void commitRename()}
            onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
          />
        ) : (
          <h3 className="intel-mm-list-title">{list.name}</h3>
        )}
        <span className="intel-mm-list-count">{listItems.length}</span>
        {system && <span className="intel-mm-list-default">DEFAULT</span>}
        <div className="intel-mm-list-actions">
          <button
            type="button"
            className={`intel-mm-mute-btn${list.muted ? ' intel-mm-mute-btn--on' : ''}`}
            onClick={() => onToggleMute(list)}
            title={
              list.muted ? 'Resume — send alerts again' : 'Mute — stop alerts for this watchlist'
            }
          >
            {list.muted ? 'Muted' : 'Mute'}
          </button>
          {!system && (
            <>
              <Button variant="ghost" size="sm" onClick={() => setRenaming((r) => !r)}>
                Rename
              </Button>
              <Button variant="ghost" size="sm" onClick={() => onDelete(list)}>
                Delete watchlist
              </Button>
            </>
          )}
        </div>
      </div>

      {renameErr && <div className="intel-wlc-add-err">{renameErr}</div>}

      {(list.muted || system) && (
        <div className="intel-mm-banner">
          {list.muted
            ? system
              ? 'Muted — Lucius keeps tracking your positions but won’t send alerts for this watchlist.'
              : 'Muted — Lucius won’t send alerts for anything in this watchlist.'
            : 'Auto-synced from your holdings — every position you own is tracked here. Add price alerts to any of them.'}
        </div>
      )}

      <div className="intel-mm-detail-tabs">
        <span className="intel-mm-subtab intel-mm-subtab--active">Tickers {assets.length}</span>
        <span className="intel-mm-detail-tabs-spacer" />
        <LuciusReviewButton active={reviewOpen} onClick={() => setReviewOpen((o) => !o)} />
      </div>

      {reviewOpen && (
        <LuciusReviewPanel
          listName={list.name}
          listMuted={!!list.muted}
          items={reviewItems}
          ctx={luciusCtx}
          onFix={onFix}
          onClose={() => setReviewOpen(false)}
        />
      )}

      <div className="intel-mm-section-card">
        {assets.map((it) => (
          <DetailedAssetRow
            key={it.id}
            item={it}
            alerts={alerts.filter((a) => (a.ticker || '') === it.value)}
            tickerMeta={tickerMeta}
            tickerCcy={tickerCcy}
            owned={!system && ownedTickers.has(it.value)}
            onEdit={(i) => onEditAlerts(i, list.id)}
          />
        ))}
        {assets.length === 0 && <div className="intel-mm-empty-inline">No tickers yet.</div>}
        {/* My holdings (is_auto_sync) is auto-managed — no manual add (ADR 0018). */}
        {!system && (
          <div className="intel-mm-add-wrap">
            <AddTickerInput listId={list.id} onAdded={onRefresh} />
          </div>
        )}
      </div>
    </div>
  )
}

// ── Workspace shell ──────────────────────────────────────────────────────────

interface Props {
  watchlists: Watchlist[]
  items: WatchlistItem[]
  alerts: PriceAlert[]
  activeListId: string
  onSelectList: (id: string) => void
  tickerMeta: Record<string, TickerMeta>
  tickerCcy: Record<string, string>
  ownedTickers: Set<string>
  luciusCtx: LuciusContext
  onEditAlerts: (i: WatchlistItem, sourceListId?: string) => void
  onEditTopic: (i: WatchlistItem, sourceListId?: string) => void
  onRefresh: () => void
  onClose: () => void
  /** Monitoring & alert settings live in Settings › Intelligence; this opens it there. */
  onOpenSettings: () => void
}

export function MonitorsWorkspace({
  watchlists,
  items,
  alerts,
  activeListId,
  onSelectList,
  tickerMeta,
  tickerCcy,
  ownedTickers,
  luciusCtx,
  onEditAlerts,
  onEditTopic,
  onRefresh,
  onClose,
  onOpenSettings,
}: Props) {
  const [view, setView] = useState<string>(activeListId || 'all')
  const [confirm, setConfirm] = useState<ConfirmSpec | null>(null)
  const [createErr, setCreateErr] = useState<string | null>(null)

  // fall back if the viewed watchlist is removed
  useEffect(() => {
    if (view !== 'all' && view !== 'sources' && !watchlists.some((c) => c.id === view))
      setView('all')
  }, [watchlists, view])

  const totalMonitors = items.length
  const list = watchlists.find((c) => c.id === view)

  async function renameList(id: string, name: string) {
    const res = await authFetch(`/api/watchlists/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
    if (res.status === 409) {
      throw new Error(await errorDetail(res, 'A watchlist with that name already exists.'))
    }
    if (!res.ok) {
      throw new Error(await errorDetail(res, 'Could not rename — try again.'))
    }
    onRefresh()
  }
  async function toggleMuteList(l: Watchlist) {
    await authFetch(`/api/watchlists/${l.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ muted: !l.muted }),
    })
    onRefresh()
  }
  function requestDeleteList(l: Watchlist) {
    const n = items.filter((i) => (i.watchlist_ids ?? []).includes(l.id)).length
    setConfirm({
      title: `Delete “${l.name}”?`,
      body: `This watchlist and its ${n} monitor${n === 1 ? '' : 's'} will stop being tracked. Anything you also keep in another watchlist is unaffected.`,
      confirmLabel: 'Delete watchlist',
      onConfirm: async () => {
        await authFetch(`/api/watchlists/${l.id}`, { method: 'DELETE' })
        setConfirm(null)
        setView('all')
        onRefresh()
      },
    })
  }
  async function createList() {
    const res = await authFetch('/api/watchlists', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'New watchlist' }),
    })
    if (res.status === 409) {
      setCreateErr(await errorDetail(res, 'A watchlist with that name already exists.'))
      return
    }
    if (!res.ok) {
      setCreateErr(await errorDetail(res, 'Could not create watchlist — try again.'))
      return
    }
    const c = await res.json()
    setCreateErr(null)
    onRefresh()
    if (c?.id) {
      setView(c.id)
      onSelectList(c.id)
    }
  }

  return (
    <>
      <Modal
        open
        onClose={onClose}
        size="workspace"
        className="intel-mm-workspace"
        title="Watchlists & monitors"
        subtitle={
          <>
            {totalMonitors} monitor{totalMonitors === 1 ? '' : 's'} across {watchlists.length}{' '}
            watchlist{watchlists.length === 1 ? '' : 's'} — manage everything you track and what
            fires.
          </>
        }
      >
        <div className="intel-mm-rail">
          <button
            type="button"
            className={`intel-mm-rail-item${view === 'all' ? ' intel-mm-rail-item--active' : ''}`}
            onClick={() => setView('all')}
          >
            <span className="intel-mm-rail-label">All monitors</span>
            <span className="intel-mm-rail-count">{totalMonitors}</span>
          </button>
          <div className="intel-mm-rail-section">Watchlists</div>
          {/* EntityRow renders the host <button> itself and stamps data-entity-id + draggable
              + data-ctx-*, making a watchlist @-mentionable, right-clickable ("Ask Lucius about
              this") and draggable into the dock. `watchlist` is a registered mention type
              (services/mentions/descriptors/store_backed.py, order=11). */}
          {watchlists.map((c) => (
            <EntityRow
              key={c.id}
              as="button"
              htmlType="button"
              className={`intel-mm-rail-item${view === c.id ? ' intel-mm-rail-item--active' : ''}`}
              type="watchlist"
              id={c.id}
              label={c.name}
              item={c}
              toFields={(it) => ({
                name: it.name,
                item_count: items.filter((i) => (i.watchlist_ids ?? []).includes(it.id)).length,
              })}
              sublabel={`${items.filter((i) => (i.watchlist_ids ?? []).includes(c.id)).length} items`}
              onClick={() => {
                setView(c.id)
                onSelectList(c.id)
              }}
            >
              <span className="intel-mm-rail-label">{c.name}</span>
              {c.muted && <span className="intel-mm-rail-muted">muted</span>}
              <span className="intel-mm-rail-count">
                {items.filter((i) => (i.watchlist_ids ?? []).includes(c.id)).length}
              </span>
            </EntityRow>
          ))}
          <button type="button" className="intel-mm-rail-new" onClick={() => void createList()}>
            <Plus size={13} /> New watchlist
          </button>
          {createErr && <div className="intel-wlc-add-err">{createErr}</div>}
          <div className="intel-mm-rail-section">Settings</div>
          <button type="button" className="intel-mm-rail-item" onClick={onOpenSettings}>
            <span className="intel-mm-rail-label">Monitoring &amp; alerts</span>
            <ChevronRight size={12} aria-hidden="true" />
          </button>
          <button
            type="button"
            className={`intel-mm-rail-item${view === 'sources' ? ' intel-mm-rail-item--active' : ''}`}
            onClick={() => setView('sources')}
          >
            <span className="intel-mm-rail-label">News sources</span>
          </button>
        </div>

        <div className="intel-mm-main">
          {view === 'all' && (
            <AllMonitorsView
              watchlists={watchlists}
              items={items}
              alerts={alerts}
              tickerMeta={tickerMeta}
              tickerCcy={tickerCcy}
              ownedTickers={ownedTickers}
              onEditAlerts={onEditAlerts}
              onEditTopic={onEditTopic}
            />
          )}
          {list && (
            <ListDetailView
              list={list}
              items={items}
              alerts={alerts}
              tickerMeta={tickerMeta}
              tickerCcy={tickerCcy}
              ownedTickers={ownedTickers}
              luciusCtx={luciusCtx}
              onEditAlerts={onEditAlerts}
              onRename={renameList}
              onToggleMute={(l) => void toggleMuteList(l)}
              onDelete={requestDeleteList}
              onRefresh={onRefresh}
            />
          )}
          {view === 'sources' && (
            <div className="intel-mm-sources">
              <h3 className="intel-mm-list-title">News sources</h3>
              <p className="intel-mm-settings-sub">{NEWS_SOURCES_SUBTITLE}</p>
              <NewsSourcesPanel embedded />
            </div>
          )}
        </div>
      </Modal>
      {confirm && <ConfirmDialog spec={confirm} onCancel={() => setConfirm(null)} />}
    </>
  )
}
