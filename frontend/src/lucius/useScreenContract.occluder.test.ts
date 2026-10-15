// @vitest-environment jsdom
/**
 * Tests for the edge-occluder boundary logic in useVisibleIds.
 *
 * useVisibleIds reads [data-ctx-occluder][data-ctx-occluder-edge] elements'
 * live getBoundingClientRect() to derive the "invisible zone" carved out by
 * fixed/sticky chrome (topbar, sidebar). Rows whose geometry falls entirely
 * within that zone must NOT appear in the returned visibleIds set.
 *
 * We stub getBoundingClientRect on individual elements so the test is
 * deterministic without a real browser layout engine.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import { useVisibleIds } from './useScreenContract'

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Stub window dimensions used by the scan. */
const VW = 1440
const VH = 900
const TOPBAR_H = 56 // height of the sticky topbar in the real app

function setWindowDims() {
  Object.defineProperty(window, 'innerWidth', { value: VW, writable: true, configurable: true })
  Object.defineProperty(window, 'innerHeight', { value: VH, writable: true, configurable: true })
}

/**
 * Set up a minimal DOM with:
 *   - a topbar occluder at the top (y=0..topbarH)
 *   - a content region that starts below the topbar
 *   - rows at configurable vertical positions
 */
function setupDom(rows: Array<{ id: string; top: number; bottom: number }>) {
  document.body.innerHTML = `
    <div class="topbar"
         data-ctx-occluder
         data-ctx-occluder-edge="top"
         id="topbar">
      Topbar chrome
    </div>
    <div data-ctx-region="content" id="content">
      ${rows.map((r) => `<div data-ctx data-entity-id="${r.id}" id="${r.id}">${r.id}</div>`).join('\n')}
    </div>
  `
}

/** Assign a deterministic DOMRect to an element by id. */
function stubRect(id: string, rect: Partial<DOMRect>) {
  const el = document.getElementById(id)
  if (!el) throw new Error(`element #${id} not found`)
  const full: DOMRect = {
    top: rect.top ?? 0,
    bottom: rect.bottom ?? 0,
    left: rect.left ?? 0,
    right: rect.right ?? VW,
    width: (rect.right ?? VW) - (rect.left ?? 0),
    height: (rect.bottom ?? 0) - (rect.top ?? 0),
    x: rect.left ?? 0,
    y: rect.top ?? 0,
    toJSON: () => ({}),
  }
  vi.spyOn(el, 'getBoundingClientRect').mockReturnValue(full)
}

// ── Suite ─────────────────────────────────────────────────────────────────────

describe('useVisibleIds — edge occluder (topbar)', () => {
  beforeEach(() => {
    setWindowDims()
    vi.spyOn(window, 'getComputedStyle').mockReturnValue({
      position: 'static',
    } as unknown as CSSStyleDeclaration)
  })

  afterEach(() => {
    // Unmount the rendered hooks so their effect cleanup runs (clears the
    // pending scan timers + disconnects the MutationObserver) before the jsdom
    // environment tears down — otherwise a trailing scan fires post-teardown
    // and React's scheduler throws "window is not defined" as an uncaught error.
    cleanup()
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  it('row fully beneath the topbar occluder is visible', () => {
    setupDom([{ id: 'row-below', top: TOPBAR_H + 10, bottom: TOPBAR_H + 50 }])
    stubRect('topbar', { top: 0, bottom: TOPBAR_H, left: 0, right: VW })
    stubRect('content', { top: TOPBAR_H, bottom: VH, left: 0, right: VW })
    stubRect('row-below', { top: TOPBAR_H + 10, bottom: TOPBAR_H + 50, left: 0, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    expect(result.current.visibleIds.has('row-below')).toBe(true)
  })

  it('row fully behind the topbar occluder is NOT visible', () => {
    // Simulate having scrolled so a row is now underneath the topbar rect
    // The topbar stays at y=0..TOPBAR_H (sticky); the row is at y=10..40 (scrolled under)
    setupDom([{ id: 'row-under', top: 10, bottom: 40 }])
    stubRect('topbar', { top: 0, bottom: TOPBAR_H, left: 0, right: VW })
    // Content region itself has scrolled so its top is above the viewport
    stubRect('content', { top: -200, bottom: VH - 200, left: 0, right: VW })
    stubRect('row-under', { top: 10, bottom: 40, left: 0, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    expect(result.current.visibleIds.has('row-under')).toBe(false)
  })

  it('row mostly behind the topbar bottom edge is NOT visible (< 50% threshold)', () => {
    // Row spans y=30..60 (height 30), topbar covers y=0..56 — only 4px (~13%) peeks below.
    setupDom([{ id: 'row-partial', top: 30, bottom: 60 }])
    stubRect('topbar', { top: 0, bottom: TOPBAR_H, left: 0, right: VW })
    stubRect('content', { top: -300, bottom: VH - 300, left: 0, right: VW })
    stubRect('row-partial', { top: 30, bottom: 60, left: 0, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    // Only ~13% of the row clears the occluder edge — under the 50% threshold → not visible.
    expect(result.current.visibleIds.has('row-partial')).toBe(false)
  })

  it('without an occluder tag, scrolled-under rows are counted (confirms the bug pre-fix)', () => {
    // No data-ctx-occluder-edge — legacy behaviour: topB = max(0, contentTop)
    document.body.innerHTML = `
      <div class="topbar" id="topbar">Topbar chrome (NO occluder attr)</div>
      <div data-ctx-region="content" id="content">
        <div data-ctx data-entity-id="row-x" id="row-x">row-x</div>
      </div>
    `
    stubRect('topbar', { top: 0, bottom: TOPBAR_H, left: 0, right: VW })
    stubRect('content', { top: -200, bottom: VH - 200, left: 0, right: VW })
    // row-x sits at y=10..40 — under where the topbar would be
    stubRect('row-x', { top: 10, bottom: 40, left: 0, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    // Without the occluder, the scan uses topB=max(0,-200)=0, so the row IS counted
    // (this is the pre-fix behavior that the bug describes).
    expect(result.current.visibleIds.has('row-x')).toBe(true)
  })

  it('left-edge occluder excludes a row fully to its left', () => {
    // Sidebar at x=0..200, row at x=10..150 (fully behind sidebar)
    document.body.innerHTML = `
      <nav data-ctx-occluder data-ctx-occluder-edge="left" id="sidebar">Sidebar</nav>
      <div data-ctx-region="content" id="content">
        <div data-ctx data-entity-id="row-left" id="row-left">row-left</div>
      </div>
    `
    stubRect('sidebar', { top: 0, bottom: VH, left: 0, right: 200 })
    stubRect('content', { top: 0, bottom: VH, left: 200, right: VW })
    stubRect('row-left', { top: 100, bottom: 140, left: 10, right: 150 })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    expect(result.current.visibleIds.has('row-left')).toBe(false)
  })

  it('left-edge occluder passes a row that starts to the right of the sidebar', () => {
    document.body.innerHTML = `
      <nav data-ctx-occluder data-ctx-occluder-edge="left" id="sidebar">Sidebar</nav>
      <div data-ctx-region="content" id="content">
        <div data-ctx data-entity-id="row-right" id="row-right">row-right</div>
      </div>
    `
    stubRect('sidebar', { top: 0, bottom: VH, left: 0, right: 200 })
    stubRect('content', { top: 0, bottom: VH, left: 200, right: VW })
    stubRect('row-right', { top: 100, bottom: 140, left: 210, right: 600 })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    expect(result.current.visibleIds.has('row-right')).toBe(true)
  })
})

// ── Sticky-header occlusion (width-dependent over-count regression) ────────────
//
// A per-screen sticky header (summary cards + filters) pins at the content top.
// When the window is WIDE the header is short; when NARROW / the Lucius panel is
// open, the cards + filters WRAP and the header grows taller than half the
// viewport. The scan must STILL treat the header as an occluder so transaction
// rows scrolled up behind it aren't reported as "Visible now" — the old
// `b.height < vh/2` guard skipped a wrapped header, causing the over-count
// (14 reported vs ~6 actually visible). The fix guards on the header's BOTTOM
// (`b.bottom < vh * 0.85`) instead of its height.
describe('useVisibleIds — sticky header occlusion (wrapped/tall header)', () => {
  beforeEach(() => {
    setWindowDims()
    // Only the #header element is sticky; everything else is static.
    vi.spyOn(window, 'getComputedStyle').mockImplementation(
      (el: Element) =>
        ({
          position: (el as HTMLElement).id === 'header' ? 'sticky' : 'static',
        }) as unknown as CSSStyleDeclaration,
    )
  })

  afterEach(() => {
    cleanup()
    document.body.innerHTML = ''
    vi.restoreAllMocks()
  })

  function setupStickyDom() {
    document.body.innerHTML = `
      <div data-ctx-region="content" id="content">
        <div id="header">Summary + filters (wraps on narrow widths)</div>
        <div data-ctx data-entity-id="row-behind" id="row-behind">behind</div>
        <div data-ctx data-entity-id="row-below" id="row-below">below</div>
      </div>
    `
    stubRect('content', { top: 0, bottom: VH, left: 0, right: VW })
  }

  it('TALL wrapped header (> vh/2, < vh*0.85) occludes a row behind it', () => {
    setupStickyDom()
    // Header is 550px tall: exceeds vh/2 (450) but ends within the top 85% (765).
    stubRect('header', { top: 0, bottom: 550, left: 0, right: VW })
    // A transaction row scrolled up behind the header (top 100..140).
    stubRect('row-behind', { top: 100, bottom: 140, left: 0, right: VW })
    // A row below the header is genuinely visible.
    stubRect('row-below', { top: 600, bottom: 640, left: 0, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    expect(result.current.visibleIds.has('row-behind')).toBe(false)
    expect(result.current.visibleIds.has('row-below')).toBe(true)
  })

  it('SHORT header (wide window) also occludes a row behind it — width-independent', () => {
    setupStickyDom()
    // Wide-window header: short (240px), well under vh/2.
    stubRect('header', { top: 0, bottom: 240, left: 0, right: VW })
    stubRect('row-behind', { top: 100, bottom: 140, left: 0, right: VW })
    stubRect('row-below', { top: 300, bottom: 340, left: 0, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    expect(result.current.visibleIds.has('row-behind')).toBe(false)
    expect(result.current.visibleIds.has('row-below')).toBe(true)
  })

  it('a full-cover sticky/fixed element (bottom past the container) is NOT treated as a header (clamp guard)', () => {
    // The content region is the scroll container; clamp against its visible bottom.
    // Here the container is taller than the viewport (top 0..1000), so its visible
    // bottom is botB (VH-8 = 892). A sticky element whose bottom (950) sits past that
    // would push topB beyond the visible area — e.g. a full-screen overlay/portal
    // mounted inside the content region — so the clamp leaves topB at the top.
    document.body.innerHTML = `
      <div data-ctx-region="content" id="content">
        <div id="header">Full-cover overlay</div>
        <div data-ctx data-entity-id="row-behind" id="row-behind">behind</div>
      </div>
    `
    stubRect('content', { top: 0, bottom: 1000, left: 0, right: VW })
    stubRect('header', { top: 0, bottom: 950, left: 0, right: VW })
    stubRect('row-behind', { top: 100, bottom: 140, left: 0, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    // topB stays at the content top (0), so the row is counted (not occluded).
    expect(result.current.visibleIds.has('row-behind')).toBe(true)
  })

  it('a row just over half visible counts; just under half does not (≥50% threshold)', () => {
    document.body.innerHTML = `
      <div data-ctx-region="content" id="content">
        <div id="header">header</div>
        <div data-ctx data-entity-id="row-60" id="row-60">60% visible</div>
        <div data-ctx data-entity-id="row-40" id="row-40">40% visible</div>
      </div>
    `
    stubRect('content', { top: 0, bottom: VH, left: 0, right: VW })
    // Header bottom at y=500 sets the region top boundary.
    stubRect('header', { top: 0, bottom: 500, left: 0, right: VW })
    // row-60: 100px tall, top=460 → 60px (500..560) clears the header (60% visible) → counts.
    stubRect('row-60', { top: 460, bottom: 560, left: 0, right: VW })
    // row-40: 100px tall, top=440 → only 40px (500..540) clears the header (40% visible) → no.
    stubRect('row-40', { top: 440, bottom: 540, left: 0, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    expect(result.current.visibleIds.has('row-60')).toBe(true)
    expect(result.current.visibleIds.has('row-40')).toBe(false)
  })

  it('a row clipped only by the VIEWPORT bottom edge still counts (edge ≠ occlusion)', () => {
    // Reproduces the "5 vs 6" undercount: the last transaction row sits at the bottom of the
    // screen, partly below the fold. It's cut by the viewport edge, not hidden behind chrome —
    // the user can still see it, so it counts.
    document.body.innerHTML = `
      <div data-ctx-region="content" id="content">
        <div data-ctx data-entity-id="row-bottom" id="row-bottom">bottom row</div>
      </div>
    `
    // Content panel runs the full height of the screen, so its bottom IS the viewport edge.
    stubRect('content', { top: 0, bottom: VH, left: 0, right: VW })
    // Row straddles the viewport bottom (botB = VH-8 = 892): only ~12px of its 60px shows.
    stubRect('row-bottom', { top: 880, bottom: 940, left: 0, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    expect(result.current.visibleIds.has('row-bottom')).toBe(true)
  })

  it('a row clipped by a scroll container that ends before the viewport IS occluded', () => {
    // The container itself ends mid-screen (overflow clip), so the part below it is genuinely
    // hidden — that DOES count as occlusion, unlike the viewport edge above.
    document.body.innerHTML = `
      <div data-ctx-region="content" id="content">
        <div data-ctx data-entity-id="row-clipped" id="row-clipped">clipped</div>
        <div data-ctx data-entity-id="row-ok" id="row-ok">mostly in</div>
      </div>
    `
    // Container ends at y=400, well above the viewport bottom (892).
    stubRect('content', { top: 0, bottom: 400, left: 0, right: VW })
    // row-clipped: 60px tall, only 20px (380..400) inside the container → 33% → not visible.
    stubRect('row-clipped', { top: 380, bottom: 440, left: 0, right: VW })
    // row-ok: 60px tall, 50px (350..400) inside the container → 83% → visible.
    stubRect('row-ok', { top: 350, bottom: 410, left: 0, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    expect(result.current.visibleIds.has('row-clipped')).toBe(false)
    expect(result.current.visibleIds.has('row-ok')).toBe(true)
  })

  it('a wide table row with columns scrolled off-screen still counts (vertical fraction)', () => {
    // Regression: a transaction row is wider than the visible panel (KIND/SOURCE columns
    // overflow horizontally). 2D-area would drag it under 50% even though it's fully on screen
    // vertically. Vertical fraction must count it.
    document.body.innerHTML = `
      <div data-ctx-region="content" id="content">
        <div data-ctx data-entity-id="row-wide" id="row-wide">wide row</div>
      </div>
    `
    stubRect('content', { top: 0, bottom: VH, left: 0, right: VW })
    // Row is fully visible vertically (40px in-band) but 3000px wide vs the 1440px viewport —
    // only ~48% of its area is on-screen, but 100% of its height is.
    stubRect('row-wide', { top: 100, bottom: 140, left: 0, right: 3000 })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    expect(result.current.visibleIds.has('row-wide')).toBe(true)
  })

  it("content region's sticky header does NOT clip the nav region's rows (region-local)", () => {
    // Reproduces the "only 4 accounts" undercount: a tall content header set a GLOBAL
    // top boundary that excluded nav account rows sitting above it. Occlusion must be
    // region-local — the nav list has its own (header-less) box.
    document.body.innerHTML = `
      <div data-ctx-region="nav" id="nav">
        <div data-ctx data-entity-id="acct-1" id="acct-1">acct-1</div>
        <div data-ctx data-entity-id="acct-2" id="acct-2">acct-2</div>
      </div>
      <div data-ctx-region="content" id="content">
        <div id="header">Tall content header</div>
        <div data-ctx data-entity-id="txn-behind" id="txn-behind">behind</div>
      </div>
    `
    stubRect('nav', { top: 0, bottom: VH, left: 0, right: 280 })
    stubRect('acct-1', { top: 100, bottom: 140, left: 0, right: 280 })
    stubRect('acct-2', { top: 150, bottom: 190, left: 0, right: 280 })
    stubRect('content', { top: 0, bottom: VH, left: 280, right: VW })
    stubRect('header', { top: 0, bottom: 600, left: 280, right: VW })
    stubRect('txn-behind', { top: 120, bottom: 160, left: 280, right: VW })

    const { result } = renderHook(() => useVisibleIds())
    act(() => window.dispatchEvent(new Event('lx-rescan')))

    // Nav accounts above the CONTENT header are still visible (different region)...
    expect(result.current.visibleIds.has('acct-1')).toBe(true)
    expect(result.current.visibleIds.has('acct-2')).toBe(true)
    // ...but the content txn behind the content header is correctly occluded.
    expect(result.current.visibleIds.has('txn-behind')).toBe(false)
  })
})
