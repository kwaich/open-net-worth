/**
 * useScreenContract.ts — React hooks for the markup-driven screen snapshot engine.
 *
 * Provides:
 *   - useVisibleIds()  → { visibleIds: Set<string>, rescan: () => void }
 *     Runs the viewport scan (scroll/resize/lx-rescan listeners) and returns the
 *     current set of entity ids visible in the viewport.
 *
 *   - useRegisterHandles(handles)       — register/clear screen handles on mount/unmount
 *   - useRegisterScreenFacts(fn)        — register/clear the computed-facts hook
 *   - useRegisterScreenPrompts(fn)      — register/clear the screen-prompts hook
 *
 * All hooks are provider-safe: they no-op gracefully if used outside a React
 * tree (e.g. in unit tests), and they use the module-level registry in
 * screenContract.ts rather than a React context — keeping the engine
 * framework-agnostic while still integrating cleanly with React's lifecycle.
 *
 * Extraction-readiness (§14.8 R-EX4 / F7): ONW-specific chrome class names are
 * not hardcoded — they are parameterized via ChromeSelectors. The default equals
 * today's ONW values (.lx-dock / .lx-side / .lx-compose / .ac); a different host
 * overrides this config without forking the engine.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { registerScreenFacts, registerScreenPrompts, setHandles } from './screenContract'
import type { ScreenHandle } from '../state/screenSnapshot'
import { setLiveCommands } from './screenCommands'
import type { ScreenCommandSpec } from './screenCommands'
import { onScreenCommand } from './bus'

// ── Chrome selectors config ───────────────────────────────────────────────────

/**
 * Selectors for the host app's persistent chrome — the UI regions that occlude
 * the content area and must be excluded from the "visible now" scan.
 *
 * ONW defaults: the Lucius dock + side panel + composer + autocomplete.
 * A non-ONW host overrides this via setChromeSelectors() at app init.
 */
export interface ChromeSelectors {
  /**
   * Selector(s) for the floating dock / side panel chrome whose mutations
   * should NOT trigger a content rescan. These are the Lucius-owned regions
   * that change constantly while the assistant is streaming — ignoring them
   * prevents the MutationObserver from thrashing the scan on every token.
   *
   * Comma-separated CSS selector string (passed to `.closest()`).
   */
  ignoreMutations: string
  /**
   * Selector for the expanded dock's inner container — its rect is used to
   * exclude rows sitting entirely behind the open dock from "visible now".
   */
  dock: string
}

const DEFAULT_CHROME_SELECTORS: ChromeSelectors = {
  ignoreMutations: '.lx-dock, .lx-side, .lx-compose, .ac, [role="tooltip"], [role="menu"]',
  dock: '.lx-dock:not(.lx-collapsed) .lx-dock-inner',
}

let _chromeSelectors: ChromeSelectors = DEFAULT_CHROME_SELECTORS

/**
 * Override the chrome selectors for a non-ONW host. Call once at app init
 * before any useVisibleIds() hooks mount.
 */
export function setChromeSelectors(selectors: ChromeSelectors): void {
  _chromeSelectors = selectors
}

/**
 * Read the currently active chrome selectors. Used by useVisibleIds internally.
 */
export function getChromeSelectors(): ChromeSelectors {
  return _chromeSelectors
}

// ── Types re-exported for callers ────────────────────────────────────────────
export type ScreenFactsFn = (visibleIds: Set<string>) => [string, string][]
export type ScreenPromptsFn = (ctx: { title: string; modal: string }) => string[]

// ── useVisibleIds ─────────────────────────────────────────────────────────────

export interface UseVisibleIdsResult {
  visibleIds: Set<string>
  rescan: () => void
}

/**
 * Viewport scan — ported from prototype app.jsx lines 1228-1278.
 *
 * Tracks which [data-ctx] / [data-entity-id] rows are visible in the viewport.
 * Accounts for:
 *   - Edge occluders ([data-ctx-occluder][data-ctx-occluder-edge="top|left|..."]):
 *     fixed/sticky chrome (topbar, sidebar) whose live rects clamp the visibility
 *     boundaries — top-edge occluders raise topB, left-edge occluders raise leftB.
 *   - Real content-region top boundary (actual layout, not a hardcoded height)
 *   - Sticky/fixed headers inside the content region
 *   - The open Lucius dock (rows behind it are not "visible now")
 *   - Modal/dialog occlusion (when a modal is open, only rows inside it count)
 *   - Opted-out subtrees ([data-ctx-ignore] / [role=tooltip] / [role=menu])
 *
 * Fires on scroll (capture), resize, and the custom 'lx-rescan' event
 * so callers can trigger a rescan after async content loads.
 */
export function useVisibleIds(): UseVisibleIdsResult {
  const [visibleIds, setVisibleIds] = useState<Set<string>>(() => new Set())
  // Guards a debounced/scheduled scan firing after the host unmounts: skip the
  // state update so React never schedules work into a torn-down environment.
  const mountedRef = useRef(true)

  const scan = useCallback((): void => {
    // Guard against a debounced timer firing after the host environment is gone
    // (jsdom teardown between scheduling and firing; SSR). In a real browser
    // these globals always exist, so production behaviour is unchanged.
    if (!mountedRef.current) return
    if (typeof window === 'undefined' || typeof document === 'undefined') return
    const vh = window.innerHeight
    const vw = window.innerWidth
    const next = new Set<string>()
    const botB = vh - 8

    // Collect edge occluders — fixed/sticky chrome elements tagged with
    // [data-ctx-occluder] + [data-ctx-occluder-edge="top|left|right|bottom"].
    // These are architectural chrome (topbar, sidebar) that physically block part
    // of the viewport regardless of scroll position. We read their live rects and
    // use them to clamp the visibility boundaries below.
    let edgeTopB = 0
    let edgeLeftB = 0
    document.querySelectorAll('[data-ctx-occluder][data-ctx-occluder-edge]').forEach((el) => {
      const edge = el.getAttribute('data-ctx-occluder-edge')
      const b = el.getBoundingClientRect()
      if (edge === 'top' && b.bottom > 0 && b.top < vh) edgeTopB = Math.max(edgeTopB, b.bottom)
      if (edge === 'left' && b.right > 0 && b.left < vw) edgeLeftB = Math.max(edgeLeftB, b.right)
    })

    // Per-region visible box. Each [data-ctx-region] is its OWN scroll container with its own
    // top boundary (its sticky header), so occlusion is REGION-LOCAL: the content panel's sticky
    // header (summary cards + filters) must NOT clip the nav account-list's rows, and vice versa.
    // (A single global topB derived from the content header was excluding nav rows that sat above
    // it — the "only 4 accounts" undercount — AND letting content rows behind a wrapped header
    // leak in.) Edge occluders (window-fixed chrome) still apply to every region.
    type VisBox = { top: number; bottom: number; left: number; right: number }
    const computeRegionBox = (region: Element): VisBox => {
      const rr = region.getBoundingClientRect()
      let top = Math.max(edgeTopB, rr.top)
      // Clamp against the region's OWN visible bottom (not the viewport): a header that WRAPS on a
      // narrow window / open Lucius panel can be tall yet is still a header — as long as it doesn't
      // cover the whole region (which would push the boundary past the visible area).
      const bottom = Math.min(botB, rr.bottom)
      region.querySelectorAll('*').forEach((el) => {
        const pos = getComputedStyle(el as HTMLElement).position
        if (pos === 'sticky' || pos === 'fixed') {
          const b = el.getBoundingClientRect()
          if (b.top <= top + 1 && b.bottom > top && b.bottom < bottom) top = Math.max(top, b.bottom)
        }
      })
      return { top, bottom, left: Math.max(edgeLeftB, rr.left), right: Math.min(vw, rr.right) }
    }
    const regionBoxes = new Map<Element, VisBox>()
    document
      .querySelectorAll('[data-ctx-region]')
      .forEach((region) => regionBoxes.set(region, computeRegionBox(region)))
    // Rows outside any declared region fall back to the window viewport minus edge chrome.
    const windowBox: VisBox = { top: edgeTopB, bottom: botB, left: edgeLeftB, right: vw }

    // The open dock occludes part of the screen — rows hidden behind it aren't
    // "visible now". Query the expanded dock inner container.
    const dockEl = document.querySelector(_chromeSelectors.dock)
    const dock = dockEl ? dockEl.getBoundingClientRect() : null

    // An open modal/dialog grabs focus — only rows inside it count as visible.
    const occluders = [...document.querySelectorAll('[data-ctx-modal], [role="dialog"]')]
    const modalEl = occluders[0] ?? null
    const insideModal = (el: Element): boolean => occluders.some((o) => o.contains(el))

    // A row counts as "visible now" unless ≥50% of its HEIGHT is hidden by CHROME — a sticky
    // header / the region's own top, or a scroll container that ends before the viewport. Two
    // things deliberately do NOT count as occlusion:
    //   • the viewport edge itself — a row scrolled to the bottom of the screen is still "shown";
    //   • horizontal column overflow — a wide table row whose right columns scroll off-screen is
    //     still fully shown (so we measure height, never 2D area).
    const VISIBLE_FRACTION = 0.5
    document.querySelectorAll('[data-ctx], [data-entity-id]').forEach((el) => {
      const id = el.getAttribute('data-ctx-id') ?? el.getAttribute('data-entity-id')
      if (!id) return
      if (el.closest('[data-ctx-ignore], [role="tooltip"], [role="menu"]')) return
      // When a modal is open, only rows inside it are visible.
      if (modalEl && !insideModal(el)) return

      const r = el.getBoundingClientRect()
      const rowH = r.bottom - r.top
      if (rowH <= 0) return

      const region = el.closest('[data-ctx-region]')
      const box = (region && regionBoxes.get(region)) || windowBox
      // Must have SOME horizontal presence in the box (handles a left occluder / a row scrolled
      // entirely off to the side); horizontal column overflow itself does not disqualify a row.
      if (Math.min(r.right, box.right) - Math.max(r.left, box.left) <= 0) return
      // Must intersect the viewport vertically at all.
      if (r.bottom <= box.top || r.top >= botB) return

      // Height hidden behind chrome: above the top boundary (sticky header / region top), plus
      // below the box bottom ONLY when that bottom is a scroll-container clip, not the viewport
      // edge (box.bottom < botB ⇒ the region ends before the screen does).
      const occTop = Math.max(0, box.top - r.top)
      const occBottom = box.bottom < botB ? Math.max(0, r.bottom - box.bottom) : 0
      if ((rowH - occTop - occBottom) / rowH < VISIBLE_FRACTION) return

      // Exclude a row sitting entirely behind the open Lucius dock (bottom-right). A full-width
      // row keeps its left portion visible; only drop one with no visible part left of the dock.
      if (dock && r.left >= dock.left) {
        const vBot = Math.min(r.bottom, box.bottom, dock.top)
        if (vBot - Math.max(r.top, box.top) <= 0) return
      }

      next.add(id)
    })

    setVisibleIds((prev) => {
      if (prev.size === next.size && [...next].every((id) => prev.has(id))) return prev
      return next
    })
  }, [])

  // Expose rescan as a stable callback so callers can fire it after content loads.
  const rescan = useCallback((): void => {
    scan()
  }, [scan])

  useEffect(() => {
    mountedRef.current = true
    scan()
    const initialTo = setTimeout(scan, 200)

    const onScroll = (): void => scan()
    const onResize = (): void => scan()
    const onLxRescan = (): void => scan()

    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onResize)
    window.addEventListener('lx-rescan', onLxRescan)

    // Re-scan when screen content mounts/changes — async data loads (e.g. the
    // transactions fetch) and route content swaps land AFTER the route-change
    // rescan fires, so without this the count is empty on first open / reload
    // until the user scrolls. Trailing-debounced; mutations confined to the
    // Lucius chat UI (streaming replies, composer) are ignored so the scan
    // doesn't churn while Lucius answers.
    let mutTo: ReturnType<typeof setTimeout>
    const bumpScan = (): void => {
      clearTimeout(mutTo)
      mutTo = setTimeout(scan, 150)
    }
    const obs = new MutationObserver((muts) => {
      const relevant = muts.some((m) => {
        const t = m.target as Element | null
        return !t?.closest?.(_chromeSelectors.ignoreMutations)
      })
      if (relevant) bumpScan()
    })
    obs.observe(document.body, { childList: true, subtree: true })

    return () => {
      mountedRef.current = false
      clearTimeout(initialTo)
      clearTimeout(mutTo)
      obs.disconnect()
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
      window.removeEventListener('lx-rescan', onLxRescan)
    }
  }, [scan])

  return { visibleIds, rescan }
}

// ── useRegisterHandles ────────────────────────────────────────────────────────

/**
 * Register screen handles on mount and clear them on unmount.
 *
 * Screens call this with their deep-source handles (charts, off-screen tabs,
 * news articles). On unmount the global registry is reset to empty so the
 * stale handles don't bleed across routes.
 */
export function useRegisterHandles(handles: ScreenHandle[]): void {
  // Stable reference: only re-register when the array content changes
  // (by JSON identity, since handles are plain objects).
  const key = JSON.stringify(handles)
  const keyRef = useRef(key)

  useEffect(() => {
    keyRef.current = key
    setHandles(handles)
    return () => {
      // Clear on unmount — avoid stale handles from a previous screen.
      setHandles([])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
}

// ── useRegisterScreenFacts ────────────────────────────────────────────────────

/**
 * Register the computed-facts hook while mounted, and remove it on unmount.
 *
 * `fn` receives the set of visible entity ids (as resolved by the viewport scan)
 * and should return an array of [label, value] pairs to append to the facts list.
 * Useful for derived figures the DOM cannot declare statically (e.g. "today's
 * movers" among the currently-visible holdings).
 *
 * Gate with `active` when the component stays MOUNTED while hidden (Plan's sub-tabs do): the
 * newest registration speaks, so a hidden pane that registered last would hand Lucius its
 * figures while the user is looking at another one.
 */
export function useRegisterScreenFacts(fn: ScreenFactsFn, active = true): void {
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    if (!active) return
    // Wrap in a stable closure so we don't re-register on every render.
    return registerScreenFacts((ids) => fnRef.current(ids))
  }, [active])
}

// ── useRegisterScreenPrompts ──────────────────────────────────────────────────

/**
 * Register the screen-prompts hook while mounted, and remove it on unmount.
 *
 * `fn` receives `{ title, modal }` (current screen title + open modal title if
 * any) and should return an array of suggested prompts strings. When registered,
 * screenSuggestions() in screenContract.ts will use these instead of the generic
 * fallback. Gate with `active` exactly as `useRegisterScreenFacts` does.
 */
export function useRegisterScreenPrompts(fn: ScreenPromptsFn, active = true): void {
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    if (!active) return
    return registerScreenPrompts((ctx) => fnRef.current(ctx))
  }, [active])
}

// ── useRegisterScreenCommands ─────────────────────────────────────────────────

export interface RegisteredScreenCommand {
  spec: ScreenCommandSpec
  handler: (args: Record<string, unknown>) => void
}

/**
 * Register the visible screen's drivable commands + bind their handlers to the bus.
 * Gate with `active`: commands live only while their screen is VISIBLE — inactive
 * screens neither advertise live:true nor apply (a dispatched command parks on the
 * bus and replays when the screen activates). Cleared on unmount/deactivation.
 */
export function useRegisterScreenCommands(
  commands: RegisteredScreenCommand[],
  active = true,
): void {
  const ref = useRef(commands)
  ref.current = commands
  const key = commands.map((c) => c.spec.name).join(',') + (active ? '|1' : '|0')

  useEffect(() => {
    if (!active || ref.current.length === 0) return
    setLiveCommands(ref.current.map((c) => c.spec))
    const offs = ref.current.map((c) =>
      onScreenCommand(c.spec.name, (cmd) => {
        const entry = ref.current.find((x) => x.spec.name === cmd.name)
        entry?.handler(cmd.args)
      }),
    )
    return () => {
      setLiveCommands([])
      offs.forEach((off) => off())
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, active])
}

// ── dispatchRescan (imperative helper for non-React code) ─────────────────────

/**
 * Fire the 'lx-rescan' custom event to trigger a viewport re-scan.
 * Useful after async content loads, panel opens/closes, or tab switches.
 */
export function dispatchRescan(): void {
  window.dispatchEvent(new Event('lx-rescan'))
}
