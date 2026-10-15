// Floating Ready-to-Assign banner (owner, 2026-09-25): the hero no longer pins, so once it has
// scrolled out of view its headline figures ride in a pill at the bottom of the page instead.
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import * as PD from './planData'

const { fBase, fBaseCents } = PD

// Whether the hero card is out of view. The topbar overlays the page's top edge, so a hero
// tucked under it counts as gone — and so does one showing only a sliver: the workspace chrome
// parks the hero with its last pixel under the pinned toolbar (the shared `- 1px` sticky
// formula), which a plain "intersecting at all" test read as still on screen, so the banner
// never appeared (owner, 2026-09-25).
// A hero that isn't rendered at all is not "scrolled away": Plan keeps every sub-tab mounted and
// hides the inactive ones with display:none, so on Outlook or Cash Flow the hero reads as out of
// view while the pill — portaled to <body> — sits outside the hidden subtree (owner, 2026-09-28).
// A ResizeObserver sees the display:none flip that the IntersectionObserver may not report.
const HERO_GONE_RATIO = 0.1
function useHeroHidden(): boolean {
  const [hidden, setHidden] = useState(false)
  const [rendered, setRendered] = useState(true)
  useEffect(() => {
    let io: IntersectionObserver | null = null
    let ro: ResizeObserver | null = null
    let observed: Element | null = null
    // `force` re-measures the cut line for the SAME hero (a resize can change the header's height).
    const attach = (force = false) => {
      const hero = document.querySelector('.pl-summary')
      if (!hero || (hero === observed && !force) || typeof IntersectionObserver !== 'function')
        return
      io?.disconnect()
      observed = hero
      // In the Budget workspace the title and sub-tabs rows stay put and the hero slides under
      // them, so "out of view" starts at the sub-tabs row's bottom, not the topbar's.
      const cover =
        document.querySelector('.pl-page--workspace > .pl-mast-sticky') ??
        document.querySelector('.topbar')
      const top = cover ? Math.round(cover.getBoundingClientRect().bottom) : 0
      io = new IntersectionObserver(
        ([e]) => setHidden(!!e && e.intersectionRatio < HERO_GONE_RATIO),
        { rootMargin: `-${top}px 0px 0px 0px`, threshold: [0, HERO_GONE_RATIO] },
      )
      io.observe(hero)
      ro?.disconnect()
      if (typeof ResizeObserver === 'function') {
        ro = new ResizeObserver(() => setRendered(hero.getClientRects().length > 0))
        ro.observe(hero)
      }
    }
    attach()
    const mo = new MutationObserver(() => attach())
    mo.observe(document.body, { childList: true, subtree: true })
    const onResize = () => attach(true)
    window.addEventListener('resize', onResize)
    return () => {
      mo.disconnect()
      io?.disconnect()
      ro?.disconnect()
      window.removeEventListener('resize', onResize)
    }
  }, [])
  return hidden && rendered
}

// Centred over the budget TABLE's column (owner, 2026-09-25) — left-aligned to it instead when
// the pill is wider than the table — and never under the Ask Lucius dock pill at the
// bottom-right, shifted left to clear it when space is tight.
const anchorEl = () =>
  document.querySelector('.pl-budget-main') ??
  document.querySelector('.pl-tablewrap') ??
  document.querySelector('.pl-page')

function usePillLeft(pill: RefObject<HTMLDivElement | null>): number | null {
  const [left, setLeft] = useState<number | null>(null)
  useLayoutEffect(() => {
    const measure = () => {
      const anchor = anchorEl()
      if (!anchor) return
      const r = anchor.getBoundingClientRect()
      const w = pill.current?.offsetWidth ?? 0
      let x = r.left + Math.max(0, (r.width - w) / 2)
      const dock = document.querySelector('.lx-dock-pill')
      if (dock) x = Math.min(x, dock.getBoundingClientRect().left - 12 - w)
      setLeft(Math.round(Math.max(16, x)))
    }
    measure()
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null
    const anchor = anchorEl()
    if (ro && anchor) ro.observe(anchor)
    if (ro && pill.current) ro.observe(pill.current)
    window.addEventListener('resize', measure)
    return () => {
      ro?.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [pill])
  return left
}

export function RtaFloat({
  rta,
  overspentCount,
  underfundedCount,
  totalAvailable,
  filter,
  setFilter,
}: {
  rta: number
  overspentCount: number
  underfundedCount: number
  totalAvailable: number
  filter: string | null
  setFilter: (f: string | null) => void
}) {
  const hidden = useHeroHidden()
  const pill = useRef<HTMLDivElement>(null)
  const left = usePillLeft(pill)
  const zero = Math.abs(rta) <= 0.005
  const neg = rta < -0.005
  const toggle = (f: string) => setFilter(filter === f ? null : f)
  return createPortal(
    <div
      ref={pill}
      className={'pl-rta-float' + (hidden && left != null ? ' shown' : '')}
      style={{ left: (left ?? 0) + 'px' }}
      aria-hidden={!hidden}
      inert={!hidden}
    >
      <span className="pl-rta-float-k">Ready to assign</span>
      <span className={'pl-rta-float-v mask' + (zero ? '' : neg ? ' neg' : ' pos')}>
        {neg ? '−' : ''}
        {fBaseCents(Math.abs(rta))}
      </span>
      <span className="pl-rta-float-sep" aria-hidden="true" />
      {overspentCount > 0 && (
        <button
          className={'pl-sum-mini-stat bad as-chip' + (filter === 'overspent' ? ' on' : '')}
          onClick={() => toggle('overspent')}
        >
          <i />
          {overspentCount} overspent
        </button>
      )}
      {underfundedCount > 0 && (
        <button
          className={'pl-sum-mini-stat warn as-chip' + (filter === 'underfunded' ? ' on' : '')}
          onClick={() => toggle('underfunded')}
        >
          <i />
          {underfundedCount} underfunded
        </button>
      )}
      {overspentCount === 0 && underfundedCount === 0 && (
        <span className="pl-sum-mini-stat ok">
          <i />
          On track
        </span>
      )}
      <span className="pl-sum-mini-stat">
        <span className="mask">{fBase(totalAvailable)}</span> available
      </span>
    </div>,
    document.body,
  )
}
