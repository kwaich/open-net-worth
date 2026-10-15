// Page-level underline tab bar — for top-level section navigation on pages like
// Settings, Automations, Plan. This is NOT a replacement for SegmentedControl;
// SegmentedControl stays for compact inline filters (2–5 short options inside a
// panel or toolbar). PageTabs belongs above all panels, below the page header.
//
// Spec: docs/archive/reviews/page-tabs-and-density.md §3

import { useEffect, useRef } from 'react'

export interface PageTab<T extends string> {
  key: T
  label: string
  /** Optional count chip — e.g. "3 new". Shown after the label. */
  badge?: number | string
}

export interface PageTabsProps<T extends string> {
  tabs: PageTab<T>[]
  value: T
  onChange: (key: T) => void
  /** Accessible label for the tablist. Defaults to "Page sections". */
  ariaLabel?: string
  /** Optional extra class on the outer container (e.g. for show/hide media-query hooks). */
  className?: string
}

/**
 * Underline tab bar for primary page-section navigation.
 *
 * Keyboard: ArrowLeft/Right cycle; Home/End jump. Roving tabIndex (active = 0).
 * ARIA: role="tablist" on bar, role="tab" + aria-selected on each button.
 * The controlled section below should carry role="tabpanel" aria-labelledby={id}.
 */
export function PageTabs<T extends string>({
  tabs,
  value,
  onChange,
  ariaLabel = 'Page sections',
  className,
}: PageTabsProps<T>) {
  // A set that scrolls (Intel's watchlist tabs on a phone) keeps the active tab in view — along
  // the bar only; the page itself never scrolls for it.
  const barRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const bar = barRef.current
    const el = bar?.querySelector<HTMLElement>('[aria-selected="true"]')
    if (!bar || !el || bar.scrollWidth <= bar.clientWidth) return
    const b = bar.getBoundingClientRect()
    const e = el.getBoundingClientRect()
    if (e.left < b.left) bar.scrollLeft -= b.left - e.left + 16
    else if (e.right > b.right) bar.scrollLeft += e.right - b.right + 16
  }, [value])

  // A keyboard selection moves focus with it — the old tab drops to tabIndex -1, and focus left
  // on it would be stranded outside the tab order.
  function select(index: number) {
    const tab = tabs[index]
    if (!tab) return
    onChange(tab.key)
    barRef.current?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[index]?.focus()
  }

  return (
    <div
      ref={barRef}
      className={['ui-page-tabs', className].filter(Boolean).join(' ')}
      role="tablist"
      aria-label={ariaLabel}
    >
      {tabs.map((tab, i) => {
        const active = tab.key === value
        const id = `ui-page-tab-${tab.key}`
        return (
          <button
            key={tab.key}
            id={id}
            type="button"
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            className={`ui-page-tabs__tab${active ? ' ui-page-tabs__tab--active' : ''}`}
            onClick={() => onChange(tab.key)}
            onKeyDown={(e) => {
              const n = tabs.length
              const to =
                e.key === 'ArrowRight'
                  ? (i + 1) % n
                  : e.key === 'ArrowLeft'
                    ? (i - 1 + n) % n
                    : e.key === 'Home'
                      ? 0
                      : e.key === 'End'
                        ? n - 1
                        : null
              if (to === null) return
              e.preventDefault()
              select(to)
            }}
          >
            {tab.label}
            {tab.badge != null && (
              <span
                className={`ui-page-tabs__badge${active ? ' ui-page-tabs__badge--active' : ''}`}
              >
                {tab.badge}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
