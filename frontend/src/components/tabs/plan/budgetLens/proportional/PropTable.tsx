// budgetLens/proportional/PropTable.tsx — the live 50/30/20 budget. Same anatomy as the Zero-Based
// table: a hero, then the book's envelopes in the book's Zero-Based groups (owner, 2026-09-26) —
// each group with an optional share of income, each envelope with an optional monthly cap. Settings
// (take-home, each group's share) live behind Configure, never on the page.
import { useMemo, useState } from 'react'
import type { DragEvent } from 'react'
import { Tag } from 'lucide-react'
import { useCategories } from '../../../../../hooks/serverState'
import { STRUCTURAL_CATEGORY_KEYS } from '../../../../../hooks/useCategories'
import { useIsMobile } from '../../../../../hooks/useIsMobile'
import { Button, ErrorState, TabSkeleton } from '../../../../ui'
import { setCachedQueryData, useQueryCache } from '../../../../../state/queryCache'
import type { RawTxn } from '../../budgetActivity'
import type { RegisterFilter } from '../../budget'
import { UndoPill } from '../../UndoPill'
import { stableColor } from '../../useBudgetModel'
import { useUndoPill } from '../../useUndoPill'
import { saveMonthlyNetIncome } from '../api'
import { currentYm } from '../period'
import { fMonth } from '../../../../../utils/format'
import { EnvelopeEditor } from './EnvelopeEditor'
import { GROUPS_KEY, createGroup, fetchGroups, renameGroup, reorderGroups } from './envelopeOps'
import { PropDialogs } from './PropDialogs'
import type { PropDialog } from './PropDialogs'
import { PropHero } from './PropHero'
import { ExcludedSection, OutsideSection } from './PropOutside'
import { PropGroup } from './PropRows'
import type { GroupActions, GroupDnd, RowHandlers } from './propCells'
import { SettingsModal } from './SettingsModal'
import type { SettingsResult } from './SettingsModal'
import { applyEnvelopeEdit } from './propEdits'
import { derive } from './propMath'
import { placeAndSave, usePropDnd } from './propDnd'
import type { RowDrop } from './propDnd'
import * as api from './propApi'
import type { PropMonth } from './propApi'
import './propTable.css'

const shiftYm = (ym: string, d: number) => {
  const [y, m] = ym.split('-').map(Number)
  const t = new Date(y!, (m ?? 1) - 1 + d, 1)
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}`
}
const shortMonth = (ym: string) => fMonth(ym)
const r50 = (v: number) => Math.ceil(v / 50) * 50

const Chev = ({ dir }: { dir: 'left' | 'right' }) => (
  <svg
    width="12"
    height="12"
    viewBox="0 0 12 12"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    style={{ transform: dir === 'left' ? 'rotate(90deg)' : 'rotate(-90deg)' }}
  >
    <path d="M3 4.5l3 3 3-3" />
  </svg>
)
const SlidersIc = () => (
  <svg
    width="13"
    height="13"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
    <circle cx="16" cy="7" r="2" />
    <circle cx="10" cy="17" r="2" />
  </svg>
)
const Plus = () => (
  <svg
    width="11"
    height="11"
    viewBox="0 0 12 12"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    aria-hidden="true"
  >
    <path d="M6 2.5v7M2.5 6h7" />
  </svg>
)

export function PropTable({
  toast,
  onLucius,
  onManageCategories,
  onOpenTxn,
  onOpenRegister,
  categoriesVersion,
}: {
  toast: (msg: string) => void
  onLucius: (prompt: string) => void
  onManageCategories?: () => void
  onOpenTxn?: (raw: RawTxn) => void
  onOpenRegister?: (filter: RegisterFilter) => void
  /** Bumped by Manage categories — a rename, merge or delete there re-reads the month. */
  categoriesVersion?: number
}) {
  const [ym, setYm] = useState(currentYm)
  const q = useQueryCache(api.propMonthKey(ym), () => api.fetchPropMonth(ym), {
    version: categoriesVersion,
  })
  const groupsQ = useQueryCache(GROUPS_KEY, fetchGroups, { version: categoriesVersion })
  // Hidden categories included: an envelope can still hold one, and its chip must name it (the
  // Zero-Based table reads the same list). The editor's "Add a category" leaves them out.
  const cats = useCategories(true)
  const mobile = useIsMobile()
  // A group folds on a click; an Excluded one starts folded (its spend is outside the split).
  const [closed, setClosed] = useState<Record<string, boolean>>({})
  const [newGroup, setNewGroup] = useState<string | null>(null)
  const dnd = usePropDnd()
  const [editing, setEditing] = useState<string | null>(null)
  const [settings, setSettings] = useState(false)
  const [dialog, setDialog] = useState<PropDialog | null>(null)
  const [gDrag, setGDrag] = useState<string | null>(null)
  const [exOver, setExOver] = useState(false)
  const [gOver, setGOver] = useState<string | null>(null)
  const { undo, captureUndo, doUndo } = useUndoPill()
  const [busy, setBusy] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)

  const catById = useMemo(() => new Map((cats.data ?? []).map((c) => [c.id, c])), [cats.data])
  const catLabel = (k: string) => String(catById.get(k)?.label ?? k)
  const catColor = (k: string) => String(catById.get(k)?.color || stableColor(k))
  // Bridge a month switch with the month on screen, as Zero-Based's useBudget does: the new
  // month's key has no data for one round-trip, and the whole screen fell back to its skeleton
  // on every arrow press. Only while that key is in its INITIAL load — a failed month shows its
  // own error, never its neighbour — and dimmed + inert meanwhile (.is-switching).
  const [lastMonth, setLastMonth] = useState<PropMonth | null>(null)
  if (q.data && q.data !== lastMonth) setLastMonth(q.data)
  const m = q.data ?? (q.initialLoading ? lastMonth : null)
  const switching = !q.data && !!m
  if (!m)
    return q.error ? (
      <ErrorState message="Couldn't load your 50/30/20 month." onRetry={() => q.refresh()} />
    ) : (
      <TabSkeleton variant="proportional" label="Loading your 50/30/20 month…" header={false} />
    )
  const d = derive(m)
  const capsLocked = m.status === 'past'

  const fail = (e: unknown) =>
    toast(e instanceof Error ? e.message : 'Something went wrong — try again.')
  // An optimistic edit the server refused comes back off the screen, not just toasted.
  const revert = (e: unknown) => {
    fail(e)
    api.invalidatePropMonths()
  }
  const patch = (fn: (e: PropMonth['envelopes'][number]) => PropMonth['envelopes'][number]) =>
    setCachedQueryData<PropMonth>(api.propMonthKey(ym), (prev) =>
      prev ? { ...prev, envelopes: prev.envelopes.map(fn) } : prev!,
    )
  // Every move lands somewhere: beside the row it was dropped on, else the bottom of its group —
  // Zero-Based's own move and order, so the row sits the same on both screens.
  // Out of the split on its own, or back in — it keeps its group either way.
  const exclude = (id: string, excluded: boolean) => {
    patch((e) => (e.id === id ? { ...e, excluded } : e))
    api.setEnvelopeExcluded(id, excluded).catch(revert)
  }
  // Dropped into a group, an Excluded envelope counts again.
  const place = (id: string, groupId: string, target: RowDrop | null) => {
    if (m.envelopes.find((e) => e.id === id)?.excluded) exclude(id, false)
    placeAndSave(api.propMonthKey(ym), m.envelopes, id, groupId, target, revert)
  }
  const drop = dnd.bind(place)
  const exDrop = {
    onDragOver: (e: DragEvent) => {
      if (!dnd.drag) return
      e.preventDefault()
      if (!exOver) setExOver(true)
    },
    onDragLeave: (e: DragEvent) => {
      if (!e.currentTarget.contains(e.relatedTarget as Node)) setExOver(false)
    },
    onDrop: (e: DragEvent) => {
      e.preventDefault()
      if (dnd.drag && !m.envelopes.find((x) => x.id === dnd.drag)?.excluded) exclude(dnd.drag, true)
      setExOver(false)
      dnd.end()
    },
  }
  const cap = (id: string, v: number) => {
    patch((e) => (e.id === id ? { ...e, cap: v || null } : e))
    api.setCap(id, ym, v).catch(revert)
  }
  const capsFromAverage = () => {
    const caps: Record<string, number> = {}
    for (const e of d.counted.flatMap((f) => f.rows))
      if (e.history.avg3 > 0) caps[e.id] = r50(e.history.avg3)
    if (Object.keys(caps).length) api.setCaps(ym, caps).catch(fail)
  }
  const run = async (fn: () => Promise<void>) => {
    setBusy(true)
    setEditError(null)
    try {
      await fn()
      setEditing(null)
    } catch (e) {
      setEditError(e instanceof Error ? e.message : 'Could not save — try again.')
    } finally {
      setBusy(false)
    }
  }
  const closeSettings = async (next: SettingsResult) => {
    setSettings(false)
    try {
      if (next.income !== (m.income.profile ?? 0)) {
        await saveMonthlyNetIncome(next.income)
        api.invalidatePropMonths()
      }
      const targets: Record<string, number | null> = {}
      for (const g of m.groups)
        if ((next.targets[g.id] ?? null) !== g.target) targets[g.id] = next.targets[g.id] ?? null
      if (Object.keys(targets).length) await api.setGroupTargets(targets)
      for (const g of m.groups)
        if (!!next.excluded[g.id] !== g.excluded)
          await api.setGroupExcluded(g.id, !!next.excluded[g.id])
      const was = m.income_categories.filter((c) => c.counted).map((c) => c.id)
      const now = next.counted ?? m.income_categories.map((c) => c.id)
      if (was.join() !== now.join()) await api.setIncomeCategories(next.counted)
    } catch (e) {
      fail(e)
    }
  }

  const h: RowHandlers = {
    month: ym,
    capsLocked,
    mobile,
    dragging: dnd.drag,
    overRow: dnd.overRow,
    catLabel,
    catColor,
    onDragStart: dnd.start,
    onDragEnd: dnd.end,
    onRowOver: drop.rowOver,
    onRowDrop: drop.rowDrop,
    onEdit: setEditing,
    onCap: cap,
    onRemoveCategory: (envId, categoryId) => {
      const env = m.envelopes.find((e) => e.id === envId)
      if (env) setDialog({ kind: 'remove', env, categoryId })
    },
    onOpenTxn,
    onOpenRegister,
  }
  const patchGroup = (
    id: string,
    fn: (g: PropMonth['groups'][number]) => PropMonth['groups'][number],
  ) =>
    setCachedQueryData<PropMonth>(api.propMonthKey(ym), (prev) =>
      prev ? { ...prev, groups: prev.groups.map((g) => (g.id === id ? fn(g) : g)) } : prev!,
    )
  // Zero-Based's group reorder, over EVERY group — a group this screen doesn't list (Commitments,
  // Credit Card Payments, income) keeps its place instead of falling to the end.
  const moveGroup = (id: string, onto: string) => {
    const all = (groupsQ.data ?? []).map((g) => g.id)
    const from = all.indexOf(id)
    if (id === onto || from < 0 || !all.includes(onto)) return
    const next = all.filter((x) => x !== id)
    next.splice(next.indexOf(onto) + (from < all.indexOf(onto) ? 1 : 0), 0, id)
    const rank = (x: string) => next.indexOf(x)
    setCachedQueryData<PropMonth>(api.propMonthKey(ym), (prev) =>
      prev ? { ...prev, groups: [...prev.groups].sort((a, b) => rank(a.id) - rank(b.id)) } : prev!,
    )
    reorderGroups(next).catch(revert)
  }
  const gd: GroupDnd = {
    dragging: gDrag,
    over: gOver,
    start: (id) => (e) => {
      e.stopPropagation()
      e.dataTransfer.effectAllowed = 'move'
      e.dataTransfer.setData('text/plain', 'group:' + id)
      setGDrag(id)
    },
    onOver: (id) => (e) => {
      if (!gDrag) return
      e.preventDefault()
      e.stopPropagation()
      if (gOver !== id) setGOver(id)
    },
    onDrop: (id) => (e) => {
      if (!gDrag) return
      e.preventDefault()
      e.stopPropagation()
      moveGroup(gDrag, id)
      setGDrag(null)
      setGOver(null)
    },
    end: () => {
      setGDrag(null)
      setGOver(null)
    },
  }
  const actions: GroupActions = {
    onAddEnvelope: (groupId, groupName) => setDialog({ kind: 'new', groupId, groupName }),
    onSetShares: () => setSettings(true),
    onToggleExcluded: (id, excluded) => {
      patchGroup(id, (g) => ({ ...g, excluded }))
      api.setGroupExcluded(id, excluded).catch(revert)
    },
    onRename: (id, name) => {
      patchGroup(id, (g) => ({ ...g, name }))
      renameGroup(id, name).catch(revert)
    },
  }
  const groups = d.groups.map((f) => (
    <PropGroup
      key={f.g.id}
      f={f}
      closed={closed[f.g.id] ?? f.g.excluded}
      onToggle={() => setClosed((c) => ({ ...c, [f.g.id]: !(c[f.g.id] ?? f.g.excluded) }))}
      income={d.income}
      dragOver={dnd.over === f.g.id}
      dropProps={drop.groupProps(f.g.id)}
      actions={actions}
      gd={gd}
      h={h}
    />
  ))
  const below = (
    <>
      <OutsideSection m={m} h={h} />
      <ExcludedSection rows={d.leftOut} h={h} dragOver={exOver} dropProps={exDrop} />
    </>
  )
  const addGroup = () => {
    const name = (newGroup ?? '').trim()
    setNewGroup(null)
    if (name) createGroup(name).catch(fail)
  }
  const env = editing ? m.envelopes.find((e) => e.id === editing) : undefined
  // Only groups a spending envelope can move into: the server refuses a synced group
  // (Commitments, Credit Card Payments).
  const closedGroups = new Set((groupsQ.data ?? []).filter((g) => g.synced).map((g) => g.name))
  const groupNames = [
    ...new Set(
      [...(groupsQ.data ?? []).map((g) => g.name), ...m.envelopes.map((e) => e.group)].filter(
        (n) => n && !closedGroups.has(n),
      ),
    ),
  ]

  // Zero-Based's New envelope form (PropDialogs.tsx): pick still-unlinked categories, or none;
  // a new group is a Zero-Based group too, so it shows on both screens.
  const addControl = (
    <div className="pt-add">
      <button type="button" className="bo-linkbtn" onClick={() => setDialog({ kind: 'new' })}>
        <Plus /> Add envelope
      </button>
      {newGroup === null ? (
        <button type="button" className="bo-linkbtn" onClick={() => setNewGroup('')}>
          <Plus /> Add group
        </button>
      ) : (
        <input
          autoFocus
          className="pt-gname-in"
          value={newGroup}
          placeholder="Group name"
          aria-label="New group name"
          onChange={(e) => setNewGroup(e.target.value)}
          onBlur={addGroup}
          onKeyDown={(e) => {
            if (e.key === 'Enter') addGroup()
            else if (e.key === 'Escape') setNewGroup(null)
          }}
        />
      )}
    </div>
  )
  const categoryMeta = (cats.data ?? []).map((c) => ({
    id: c.id,
    label: String(c.label ?? c.id),
    kind: String(c.kind ?? 'expense'),
    color: String(c.color || stableColor(c.id)),
  }))
  const hiddenKeys = new Set((cats.data ?? []).filter((c) => c.hidden).map((c) => c.id))

  return (
    <div className="pt">
      <div className="pt-bar">
        <span className="pt-month">
          <button
            type="button"
            aria-label="Previous month"
            onClick={() => setYm((v) => shiftYm(v, -1))}
          >
            <Chev dir="left" />
          </button>
          <span className="pt-month-l">{shortMonth(ym)}</span>
          <button type="button" aria-label="Next month" onClick={() => setYm((v) => shiftYm(v, 1))}>
            <Chev dir="right" />
          </button>
        </span>
        <span className="bo-spacer" />
        {/* The app's own dense secondary Button (FIRE Settings wears the same) — the design's
            .pl-btn was the prototype's global button style, a different face in this app. On a
            phone both drop to their icons so the bar stays one row (owner, 2026-09-25). */}
        <Button
          variant="secondary"
          size="dense"
          className="pt-barbtn"
          iconLeft={<SlidersIc />}
          aria-label="Configure"
          title="Configure 50/30/20"
          onClick={() => setSettings(true)}
        >
          <span className="pt-barbtn-l">Configure</span>
        </Button>
        {onManageCategories && (
          <Button
            variant="secondary"
            size="dense"
            className="pt-barbtn"
            iconLeft={<Tag size={13} strokeWidth={2} aria-hidden="true" />}
            aria-label="Manage categories"
            title="Manage categories"
            onClick={onManageCategories}
          >
            <span className="pt-barbtn-l">Manage categories</span>
          </Button>
        )}
      </div>

      <div className={'pt-body' + (switching ? ' is-switching' : '')} aria-busy={switching}>
        <PropHero
          m={m}
          d={d}
          onLucius={onLucius}
          onConfigure={() => setSettings(true)}
          onCapsFromAverage={capsLocked ? undefined : capsFromAverage}
        />

        {/* A phone gets Zero-Based's own card list (PropCards.tsx) — its figures carry their own
            labels, so no header row. */}
        {mobile ? (
          <div className="pl-mlist pt-mlist">
            {groups}
            {below}
            {addControl}
          </div>
        ) : (
          <div className="pt-layout">
            <div className="pl-tablewrap pt-table">
              <div className="pl-thead-clip">
                <div className="pl-thead pl-trow">
                  <span>Envelope</span>
                  <span className="num">Cap</span>
                  <span className="num">Activity</span>
                  <span className="num">Left</span>
                </div>
              </div>
              <div className="pl-tbody-wrap">
                <div className="pl-tbody">
                  {groups}
                  {below}
                  {addControl}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {env && (
        <EnvelopeEditor
          env={env}
          envs={m.envelopes}
          allCats={(cats.data ?? [])
            .filter((c) => c.kind !== 'income' && !c.hidden && !STRUCTURAL_CATEGORY_KEYS.has(c.id))
            .map((c) => ({ id: c.id }))}
          catLabel={catLabel}
          catColor={catColor}
          groups={groupNames}
          cap={{
            value: env.cap ?? 0,
            locked: capsLocked,
            from: shortMonth(ym),
            last: m.status === 'future' ? 0 : env.history.last,
            avg: m.status === 'future' ? 0 : env.history.avg3,
          }}
          busy={busy}
          error={editError}
          onClose={() => {
            setEditing(null)
            setEditError(null)
          }}
          onSave={(dd) =>
            run(() =>
              applyEnvelopeEdit(env, dd, {
                ym,
                capsLocked,
                groups: groupsQ.data ?? [],
                envs: m.envelopes,
              }),
            )
          }
          onMerge={() => {
            setEditing(null)
            setDialog({ kind: 'merge', env })
          }}
          onDelete={() => {
            setEditing(null)
            setDialog({ kind: 'delete', env })
          }}
        />
      )}
      <PropDialogs
        dialog={dialog}
        m={m}
        ym={ym}
        monthLabel={shortMonth(ym)}
        capsLocked={capsLocked}
        categories={categoryMeta}
        hiddenKeys={hiddenKeys}
        catLabel={catLabel}
        toast={toast}
        captureUndo={captureUndo}
        onClose={() => setDialog(null)}
      />
      <UndoPill undo={undo} onUndo={() => doUndo(toast)} />
      {settings && (
        <SettingsModal
          income={m.income.profile ?? 0}
          groups={d.groups}
          incomeCategories={m.income_categories}
          onClose={(next) => void closeSettings(next)}
        />
      )}
    </div>
  )
}
