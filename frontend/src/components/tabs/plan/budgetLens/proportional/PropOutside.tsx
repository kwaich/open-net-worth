// budgetLens/proportional/PropOutside.tsx — the 50/30/20 table's two sections below the groups
// (owner, 2026-09-28: "do all of them"): EXCLUDED — envelopes taken out of the split on their own
// (still in their Zero-Based group; drag one here, or untick "Count in the split" in its editor) —
// and NOT IN AN ENVELOPE — spending no row owns, uncategorized or in a category no envelope holds.
// That spend already comes out of savings; before this it had no row to be seen in.
import type { DragEvent } from 'react'
import { Tag } from 'lucide-react'
import { ActivityCell } from '../../budgetActivity'
import { fBase } from '../../planData'
import { PropCard } from './PropCards'
import { PropRow } from './PropRows'
import { Dash } from './propCells'
import type { RowHandlers } from './propCells'
import type { PropEnvelope, PropMonth } from './propApi'

const sum = (xs: number[]) => xs.reduce((t, x) => t + x, 0)

function Head({
  title,
  note,
  count,
  total,
}: {
  title: string
  note: string
  count: number
  total: number
}) {
  return (
    <div className="pl-grow pl-trow pt-grow pt-outhead">
      <span className="pl-gname">
        <span className="pl-gname-btn" style={{ cursor: 'default' }}>
          {title}
        </span>
        <span className="pl-gcount">{count}</span>
        <span className="t">{note}</span>
      </span>
      <span className="pl-c-num">
        <Dash />
      </span>
      <span className={'pl-c-num pt-num strong' + (total ? '' : ' dim')}>
        {fBase(total ? -total : 0)}
      </span>
      <span className="pl-c-num">
        <Dash />
      </span>
    </div>
  )
}

/** Envelopes out of the split one by one: dimmed, their spend uncounted; the section is a drop
 *  target for a dragged envelope (desktop). */
export function ExcludedSection({
  rows,
  h,
  dragOver,
  dropProps,
}: {
  rows: PropEnvelope[]
  h: RowHandlers
  dragOver: boolean
  dropProps: Record<string, (e: DragEvent) => void>
}) {
  if (!rows.length && !h.dragging) return null
  const total = sum(rows.map((e) => e.activity))
  if (h.mobile)
    return (
      <section className="pt-mgroup pt-group--out">
        <div className="pt-mghead">
          <div className="pt-mghead-top">
            <span className="pt-mgname">Excluded</span>
            <span className="pl-gcount">{rows.length}</span>
            <span className="pt-mgshare">outside the split · {fBase(total ? -total : 0)}</span>
          </div>
        </div>
        <div className="pt-mgbody">
          {rows.map((e) => (
            <PropCard key={e.id} e={e} h={h} />
          ))}
        </div>
      </section>
    )
  return (
    <div className={'pt-group pt-group--out' + (dragOver ? ' over' : '')} {...dropProps}>
      <Head title="Excluded" note="outside the split" count={rows.length} total={total} />
      {rows.map((e) => (
        <PropRow key={e.id} e={e} locked={false} noDrop h={h} />
      ))}
      {!rows.length && <div className="pt-dropnote">Drop here to leave it out of the split</div>}
    </div>
  )
}

interface Loose {
  key: string
  label: string
  color: string
  activity: number
  target: {
    activity: number
    label: string
    color: string
    categoryIds?: string[]
    uncategorized?: boolean
    kind?: string
  }
}

/** Uncategorized spend, then each category no envelope holds — what "not in an envelope" took
 *  from savings, one row each; its Activity opens the transactions behind it. */
export function OutsideSection({ m, h }: { m: PropMonth; h: RowHandlers }) {
  const rows: Loose[] = []
  const unc = m.unassigned.uncategorized
  if (Math.abs(unc) > 0.005)
    rows.push({
      key: '__uncategorized__',
      label: 'Uncategorized',
      color: 'var(--warn)',
      activity: unc,
      target: {
        activity: -unc,
        label: 'Uncategorized',
        color: 'var(--warn)',
        uncategorized: true,
        kind: 'expense',
      },
    })
  for (const c of m.unassigned.categories ?? [])
    rows.push({
      key: c.id,
      label: c.label,
      color: c.color || h.catColor(c.id),
      activity: c.activity,
      target: {
        activity: -c.activity,
        label: c.label,
        color: c.color || h.catColor(c.id),
        categoryIds: [c.id],
      },
    })
  if (!rows.length) return null
  const total = sum(rows.map((r) => r.activity))
  const cell = (r: Loose) => (
    <ActivityCell
      c={r.target}
      month={h.month}
      onOpenTxn={h.onOpenTxn}
      onOpenRegister={h.onOpenRegister}
    />
  )
  if (h.mobile)
    return (
      <section className="pt-mgroup">
        <div className="pt-mghead">
          <div className="pt-mghead-top">
            <span className="pt-mgname">Not in an envelope</span>
            <span className="pl-gcount">{rows.length}</span>
            <span className="pt-mgshare">not planned, so it reduces your savings</span>
          </div>
        </div>
        <div className="pt-mgbody">
          {rows.map((r) => (
            <div className="pt-mrow" key={r.key}>
              <div className="pt-mrow-top">
                <Tag size={14} strokeWidth={2} style={{ color: r.color }} aria-hidden="true" />
                <span className="pt-ename">{r.label}</span>
                <span className="pt-mrow-left">
                  <span className="pt-mk">Activity</span>
                  {cell(r)}
                </span>
              </div>
            </div>
          ))}
        </div>
      </section>
    )
  return (
    <div className="pt-group">
      <Head
        title="Not in an envelope"
        note="not planned, so it reduces your savings"
        count={rows.length}
        total={total}
      />
      {rows.map((r) => (
        <div className="pl-crow pl-trow pt-crow pt-looserow" key={r.key}>
          <span className="pt-cname">
            <Tag size={14} strokeWidth={2} style={{ color: r.color }} aria-hidden="true" />
            <span className="pt-ename">{r.label}</span>
          </span>
          <span className="pl-c-num">
            <Dash />
          </span>
          <span className="pl-c-num">{cell(r)}</span>
          <span className="pl-c-num">
            <Dash />
          </span>
        </div>
      ))}
    </div>
  )
}
