// A compact HTML legend for the Reports charts, in place of Chart.js' canvas legend — that one
// wraps onto as many lines as it has entries (eleven for Income vs expense) and cannot tell
// income from spending. Groups are labelled rows (one per side); on a narrow card each row
// keeps its first few entries on ONE line and folds the rest behind a "+N more" toggle (two
// tiers: a phone-width card keeps 2, a mid-width one 3), and a wide card shows them all
// (reports.css, `ov-sleg-*`).

import { useState } from 'react'

export interface LegendItem {
  key: string
  label: string
  /** Any CSS colour, so the swatch follows the theme. */
  color: string
}

export interface LegendGroup {
  /** "Income" / "Expenses"; omitted when the chart has a single group. */
  title?: string
  side?: 'in' | 'out'
  items: LegendItem[]
}

/** Entries kept on the line before the rest fold: a phone-width card, then a mid-width one. */
const PHONE_CAP = 2
const MID_CAP = 3

function LegendRow({ group }: { group: LegendGroup }) {
  const [open, setOpen] = useState(false)
  const { items } = group
  const more = (cap: number) =>
    items.length > cap && (
      <button
        type="button"
        className={`ov-sleg-more c${cap}`}
        aria-expanded={open}
        title={items
          .slice(cap)
          .map((h) => h.label)
          .join(', ')}
        onClick={() => setOpen((o) => !o)}
      >
        {open ? 'Show less' : `+${items.length - cap} more`}
      </button>
    )
  return (
    <div className={'ov-sleg-group' + (group.side ? ` ${group.side}` : '') + (open ? ' open' : '')}>
      {group.title && <span className="ov-sleg-title">{group.title}</span>}
      {items.map((it, i) => (
        <span
          key={it.key}
          className={'ov-sleg-item' + (i >= PHONE_CAP ? ' x2' : '') + (i >= MID_CAP ? ' x3' : '')}
          title={it.label}
        >
          <i style={{ background: it.color }} />
          <span className="ov-sleg-label">{it.label}</span>
        </span>
      ))}
      {more(MID_CAP)}
      {more(PHONE_CAP)}
    </div>
  )
}

export function SeriesLegend({ groups }: { groups: LegendGroup[] }) {
  return (
    <div className="ov-sleg">
      {groups
        .filter((g) => g.items.length > 0)
        .map((g) => (
          <LegendRow key={g.title ?? 'all'} group={g} />
        ))}
    </div>
  )
}
