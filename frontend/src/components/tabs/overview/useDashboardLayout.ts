// Overview v2 — dashboard-layout state: load/seed/persist + the mutation API
// every customize-mode interaction (add/remove/duplicate/patch/drag/resize/
// sections/views) goes through. Edits are LIVE, not a draft (mirrors the
// handoff's Customize model): every mutation updates the real state
// immediately and is persisted (debounced) to the server; `snapshot`/`restore`
// exist only so Customize mode's Cancel/Reset-changes can undo every view back
// to the moment editing was entered. The currently selected view is navigation,
// not an edit, so restore keeps it whenever that view still exists.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { fetchDashboardLayout, saveDashboardLayout } from '../../../api/dashboard'
import {
  fetchCachedQuery,
  getCachedQueryData,
  invalidateQueryCache,
} from '../../../state/queryCache'
import { PICKER_DEFINITIONS, PRESET_VIEWS, WIDGET_DEFINITIONS, findDefinition } from './definitions'
import { compact, localInstanceId, normalize, seedConfig, seedWidgets } from './grid'
import { NEW_ITEM_PULSE_MS } from './layoutConstants'
import type {
  DashboardState,
  DashboardView,
  GridItem,
  SectionDivider,
  WidgetInstance,
} from './types'
import { isSection } from './types'

const SAVE_DEBOUNCE_MS = 700
const DASHBOARD_LAYOUT_KEY = '/api/dashboard/layout'

// Which view is open is remembered for the VISIT (sessionStorage), not forever: within a visit
// the Dashboard returns to the view you picked, a fresh visit opens on the first view, your
// home (owner, 2026-10-10 — same rule as Plan's tabs). The saved layout still carries its own
// activeViewId; it no longer decides where a new visit lands.
const VISIT_VIEW_KEY = 'ov-active-view'
function visitView(): string | null {
  try {
    return sessionStorage.getItem(VISIT_VIEW_KEY)
  } catch {
    return null
  }
}
function openingView(views: DashboardView[]): string | null {
  const v = visitView()
  return views.some((x) => x.viewId === v) ? v : (views[0]?.viewId ?? null)
}

function buildPresetState(): DashboardState {
  const views: DashboardView[] = PRESET_VIEWS.map((p) => ({
    viewId: localInstanceId('view'),
    name: p.name,
    widgets: seedWidgets(p.widgets, WIDGET_DEFINITIONS),
  }))
  return { activeViewId: views[0]?.viewId ?? null, views }
}

export function useDashboardLayout() {
  const cachedLayout = getCachedQueryData<DashboardState>(DASHBOARD_LAYOUT_KEY)
  const [state, setState] = useState<DashboardState>(() =>
    cachedLayout
      ? { ...cachedLayout, activeViewId: openingView(cachedLayout.views) }
      : { activeViewId: null, views: [] },
  )
  const [loading, setLoading] = useState(cachedLayout === undefined)
  const [error, setError] = useState<string | null>(null)
  const snapshotRef = useRef<DashboardState | null>(null)
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const skipNextSave = useRef(true) // don't save right after the initial load/seed
  // `PUT /api/dashboard/layout` is a full-document REPLACE, not a merge. If the
  // initial GET fails, the hook falls back to a local preset so the tab is
  // still usable — but autosaving that fallback would silently overwrite the
  // user's real saved layout the moment they make any edit. This gate stays
  // false for the whole session on a failed load: the user can still use the
  // dashboard locally, it just never pushes to the server until a reload
  // actually reaches a real (or genuinely-empty, server-confirmed) layout.
  const canSaveRef = useRef(false)
  const stateRef = useRef(state)
  stateRef.current = state

  useEffect(() => {
    let cancelled = false
    // `force: true` — this key has no `useQueryCache` subscribers anywhere in the app, so a
    // save's `invalidateQueryCache` below never triggers a real re-fetch (nothing is listening)
    // and the localStorage-hydrated snapshot used for instant paint can otherwise sit stale for
    // the rest of the session. `activeViewId` is exactly the state a reload must get right, so
    // this mount always confirms it against the server rather than trusting that mirror — the
    // `cachedLayout` seed above still paints instantly while this round trip is in flight.
    fetchCachedQuery(DASHBOARD_LAYOUT_KEY, fetchDashboardLayout, { force: true })
      .then((data) => {
        if (cancelled) return
        canSaveRef.current = true
        if (data.views.length) {
          setState({
            activeViewId: openingView(data.views),
            // `normalize`, not a bare `compact`: compact only resolves OVERLAPS between
            // items that already have geometry. A saved view whose widgets carry only a
            // `defId` — a seeded layout, a hand-edited one, anything written by something
            // other than this hook — reaches GridCard with `w`/`h` undefined, which
            // renders `width: calc(NaN%)` and `top: undefined`: every tile collapses to
            // content width and stacks at one point. `normalize` fills each missing field
            // from the widget's own definition and then compacts, so it is a strict
            // superset of what was here and a no-op for a fully-specified layout.
            views: data.views.map((v) => ({
              ...v,
              widgets: normalize(v.widgets, WIDGET_DEFINITIONS),
            })),
          })
        } else {
          setState(buildPresetState())
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(
            'Could not load your dashboard layout — changes in this session will not be saved until you reload successfully.',
          )
          setState(buildPresetState())
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Debounced persistence — every mutation below goes through setState, so this
  // single effect is the one save path.
  useEffect(() => {
    if (loading || !canSaveRef.current) return
    if (skipNextSave.current) {
      skipNextSave.current = false
      return
    }
    if (saveTimer.current) clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(() => {
      // Cleared as it fires: the unload flush reads a non-null timer as "a save is
      // still pending", and a stale handle made every reload re-send this page's copy.
      saveTimer.current = null
      saveDashboardLayout(state)
        .then(() => invalidateQueryCache(DASHBOARD_LAYOUT_KEY))
        .catch(() => {
          setError('Could not save your dashboard changes — they may not survive a reload.')
        })
    }, SAVE_DEBOUNCE_MS)
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, loading])

  // Flush a pending debounced save immediately on unmount (route-away within
  // the SPA) or tab close — otherwise an edit made within the debounce window
  // of navigating away is silently lost. Best-effort: fire-and-forget, no
  // await possible in either teardown path. `keepalive` is required here
  // specifically: a `beforeunload`-triggered fetch without it is routinely
  // aborted mid-flight once the browser starts tearing down the document
  // (e.g. a hard refresh), which silently dropped this exact flush and left
  // the server on a stale `activeViewId` from an earlier, already-landed save.
  useEffect(() => {
    const flush = () => {
      if (saveTimer.current && canSaveRef.current) {
        clearTimeout(saveTimer.current)
        saveTimer.current = null
        saveDashboardLayout(stateRef.current, { keepalive: true })
          .then(() => invalidateQueryCache(DASHBOARD_LAYOUT_KEY))
          .catch(() => {})
      }
    }
    window.addEventListener('beforeunload', flush)
    return () => {
      flush()
      window.removeEventListener('beforeunload', flush)
    }
  }, [])

  const activeView = useMemo(
    () => state.views.find((v) => v.viewId === state.activeViewId) ?? state.views[0] ?? null,
    [state],
  )

  // Takes an UPDATER (prev widgets -> next widgets), not a precomputed array,
  // and applies it inside `setState`'s own functional form — so N mutator
  // calls synchronously queued in the same render pass (e.g. the auto-height
  // effect correcting several content-fit widgets' heights off one
  // ResizeObserver batch) each read the PREVIOUS call's result rather than a
  // shared stale `activeView.widgets` closure, which used to let all but the
  // last call in a burst get silently discarded (harness audit finding).
  const setActiveWidgets = useCallback(
    (viewId: string, updater: (widgets: GridItem[]) => GridItem[]) => {
      setState((s) => ({
        ...s,
        views: s.views.map((v) =>
          v.viewId === viewId ? { ...v, widgets: compact(updater(v.widgets)) } : v,
        ),
      }))
    },
    [],
  )

  useEffect(() => {
    if (!state.activeViewId) return
    try {
      sessionStorage.setItem(VISIT_VIEW_KEY, state.activeViewId)
    } catch {
      /* storage blocked: the view simply isn't remembered for the visit */
    }
  }, [state.activeViewId])

  const setActiveViewId = useCallback((viewId: string) => {
    setState((s) => ({ ...s, activeViewId: viewId }))
  }, [])

  const addView = useCallback((name: string) => {
    const view: DashboardView = {
      viewId: localInstanceId('view'),
      name: name || 'New view',
      widgets: [],
    }
    setState((s) => ({ activeViewId: view.viewId, views: [...s.views, view] }))
  }, [])

  const setPhoneOrder = useCallback((viewId: string, phoneOrder: string[]) => {
    setState((s) => ({
      ...s,
      views: s.views.map((v) => (v.viewId === viewId ? { ...v, phoneOrder } : v)),
    }))
  }, [])

  const renameView = useCallback((viewId: string, name: string) => {
    setState((s) => ({
      ...s,
      views: s.views.map((v) => (v.viewId === viewId ? { ...v, name } : v)),
    }))
  }, [])

  // Never delete the default (first) view, and never delete the last
  // remaining view — mirrors the handoff's `deleteView` guard exactly
  // (`vs.views.length <= 1 || vs.views[0].id === id) return vs`).
  const deleteView = useCallback((viewId: string) => {
    setState((s) => {
      if (s.views.length <= 1 || s.views[0]?.viewId === viewId) return s
      const views = s.views.filter((v) => v.viewId !== viewId)
      const activeViewId = s.activeViewId === viewId ? (views[0]?.viewId ?? null) : s.activeViewId
      return { activeViewId, views }
    })
  }, [])

  /** Drag-to-reorder the view tabs (item 11). */
  const reorderViews = useCallback((fromViewId: string, toViewId: string) => {
    if (fromViewId === toViewId) return
    setState((s) => {
      const arr = [...s.views]
      const fi = arr.findIndex((v) => v.viewId === fromViewId)
      const ti = arr.findIndex((v) => v.viewId === toViewId)
      if (fi < 0 || ti < 0) return s
      const [moved] = arr.splice(fi, 1)
      if (!moved) return s
      arr.splice(ti, 0, moved)
      return { ...s, views: arr }
    })
  }, [])

  // The uid of the widget/section most recently landed via addWidget/
  // addSection — GridCard reads this to apply the new-item accent pulse +
  // scroll-into-view (item 11/20), and it self-clears after the pulse.
  const [newlyAddedId, setNewlyAddedId] = useState<string | null>(null)
  const pulseTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const clearNewlyAdded = useCallback(() => setNewlyAddedId(null), [])
  useEffect(
    () => () => {
      if (pulseTimer.current) clearTimeout(pulseTimer.current)
    },
    [],
  )
  const announceNew = useCallback((instanceId: string) => {
    setNewlyAddedId(instanceId)
    if (pulseTimer.current) clearTimeout(pulseTimer.current)
    pulseTimer.current = setTimeout(() => setNewlyAddedId(null), NEW_ITEM_PULSE_MS)
  }, [])

  const addWidget = useCallback(
    (defId: string) => {
      if (!activeView) return
      const def = findDefinition(defId)
      if (!def) return
      const instanceId = localInstanceId(defId)
      setActiveWidgets(activeView.viewId, (widgets) => {
        const maxY = widgets.reduce((m, w) => Math.max(m, w.y + w.h), 0)
        const instance: WidgetInstance = {
          instanceId,
          defId,
          x: 0,
          y: maxY,
          w: def.dsize,
          h: def.dh,
          config: seedConfig(def, undefined),
          limit: null,
        }
        return [...widgets, instance]
      })
      announceNew(instanceId)
      return instanceId
    },
    [activeView, setActiveWidgets, announceNew],
  )

  const addSection = useCallback(
    (title: string) => {
      if (!activeView) return
      const instanceId = localInstanceId('section')
      setActiveWidgets(activeView.viewId, (widgets) => {
        const maxY = widgets.reduce((m, w) => Math.max(m, w.y + w.h), 0)
        return [
          ...widgets,
          { kind: 'section', instanceId, title: title || 'Section', x: 0, y: maxY, w: 12, h: 30 },
        ]
      })
      announceNew(instanceId)
      return instanceId
    },
    [activeView, setActiveWidgets, announceNew],
  )

  const removeItem = useCallback(
    (instanceId: string) => {
      if (!activeView) return
      setActiveWidgets(activeView.viewId, (widgets) =>
        widgets.filter((w) => w.instanceId !== instanceId),
      )
    },
    [activeView, setActiveWidgets],
  )

  const duplicateItem = useCallback(
    (instanceId: string) => {
      if (!activeView) return
      setActiveWidgets(activeView.viewId, (widgets) => {
        const src = widgets.find((w) => w.instanceId === instanceId)
        if (!src || isSection(src)) return widgets
        const clone: WidgetInstance = {
          ...src,
          instanceId: localInstanceId(src.defId),
          y: src.y + src.h,
        }
        return [...widgets, clone]
      })
    },
    [activeView, setActiveWidgets],
  )

  const patchItem = useCallback(
    (instanceId: string, patch: Partial<WidgetInstance> & Partial<SectionDivider>) => {
      if (!activeView) return
      setActiveWidgets(activeView.viewId, (widgets) =>
        widgets.map((w) => (w.instanceId === instanceId ? ({ ...w, ...patch } as GridItem) : w)),
      )
    },
    [activeView, setActiveWidgets],
  )

  /** Commit a drag gesture: apply the FULL set of positions the live preview
   * already reflowed to (DashboardGrid's `compact(..., dragKey)` during
   * pointermove) onto the real widgets, then a final plain compact — mirrors
   * the handoff's `up()` handler, which applies `dd.positions` and re-runs
   * `compact(next, dd.uid)` with NO dragKey (the positions are already where
   * they belong; the tie-break just keeps the dragged item pinned on ties). */
  const dragItem = useCallback(
    (positions: Record<string, { x: number; y: number }>, primaryInstanceId: string) => {
      if (!activeView) return
      setActiveWidgets(activeView.viewId, (widgets) => {
        const next = widgets.map((w) =>
          positions[w.instanceId]
            ? { ...w, x: positions[w.instanceId]!.x, y: positions[w.instanceId]!.y }
            : w,
        )
        return compact(next, primaryInstanceId)
      })
    },
    [activeView, setActiveWidgets],
  )

  /** Commit a resize gesture. DashboardGrid's live preview already computed
   * the final x/y/w/h (edge-direction math, snap-to-neighbor, and the
   * topmost-open-slot lift for a width-only resize) — this just applies it
   * and re-compacts, mirroring the handoff's resize `up()` (`setWidgets((prev)
   * => compact(prev))`). */
  const resizeItem = useCallback(
    (instanceId: string, x: number, y: number, w: number, h: number) => {
      if (!activeView) return
      setActiveWidgets(activeView.viewId, (widgets) => {
        const resized = widgets.map((it) =>
          it.instanceId === instanceId ? { ...it, x, y, w, h } : it,
        )
        return compact(resized)
      })
    },
    [activeView, setActiveWidgets],
  )

  // Add a widget pre-seeded with extra config, layered over the def's own
  // `seedConfig` defaults — kept SEPARATE from `addWidget` (never widening its
  // (defId) => id contract) so every existing call site is untouched. Added
  // for the Add-widget picker's "Build with Lucius" flow (AddWidgetPicker.tsx,
  // parity item 1/2): stashes the user's prompt onto a `lux-html`/`lux-chart`
  // instance the moment it lands, so content.tsx's renderer has it on first
  // paint instead of waiting for a second edit.
  const addWidgetWithConfig = useCallback(
    (defId: string, extraConfig: Record<string, unknown>) => {
      if (!activeView) return
      const def = findDefinition(defId)
      if (!def) return
      const instanceId = localInstanceId(defId)
      setActiveWidgets(activeView.viewId, (widgets) => {
        const maxY = widgets.reduce((m, w) => Math.max(m, w.y + w.h), 0)
        const instance: WidgetInstance = {
          instanceId,
          defId,
          x: 0,
          y: maxY,
          w: def.dsize,
          h: def.dh,
          config: { ...seedConfig(def, undefined), ...extraConfig },
          limit: null,
        }
        return [...widgets, instance]
      })
      announceNew(instanceId)
      return instanceId
    },
    [activeView, setActiveWidgets, announceNew],
  )

  const snapshot = useCallback(() => {
    snapshotRef.current = state
  }, [state])

  const restoreSnapshot = useCallback(() => {
    const snapshot = snapshotRef.current
    if (!snapshot) return
    setState((current) => ({
      ...snapshot,
      activeViewId: snapshot.views.some((view) => view.viewId === current.activeViewId)
        ? current.activeViewId
        : snapshot.activeViewId,
    }))
  }, [])

  const isDirty = useMemo(() => {
    if (!snapshotRef.current) return false
    return JSON.stringify(snapshotRef.current.views) !== JSON.stringify(state.views)
  }, [state])

  return {
    loading,
    error,
    dismissError: () => setError(null),
    state,
    views: state.views,
    activeView,
    activeViewId: state.activeViewId,
    setActiveViewId,
    addView,
    renameView,
    setPhoneOrder,
    deleteView,
    reorderViews,
    addWidget,
    addWidgetWithConfig,
    addSection,
    removeItem,
    duplicateItem,
    patchItem,
    dragItem,
    resizeItem,
    newlyAddedId,
    clearNewlyAdded,
    snapshot,
    restoreSnapshot,
    isDirty,
    pickerDefinitions: PICKER_DEFINITIONS,
  }
}

export type UseDashboardLayout = ReturnType<typeof useDashboardLayout>
