// budgetLens/proportional/PropCards.tsx — the 50/30/20 table on a phone. Each group is ONE card
// with its envelopes as rows inside it (owner, 2026-09-26: "it lacks depth, lacks structure — hard
// to know which one is group"): a tinted head carries the group's own figures; a row reads its name
// and Left on one line, a bar of spend against its cap, then its cap and activity — the same leaf
// cells the desktop row uses (propCells.tsx), so a cap is edited and activity opened the same way.
import { useState } from 'react'
import { entityRefProps } from '../../../../../utils/mentionable'
import { ColorIcon, InlineEdit } from '../../budgetParts'
import { Chevron, Plus } from '../../icons'
import { fBase } from '../../planData'
import { groupFigures } from './propMath'
import type { GroupFigures } from './propMath'
import {
  ActivityFor,
  CapBar,
  CapCell,
  CatsChip,
  GroupKebab,
  GroupShare,
  LeftCell,
} from './propCells'
import type { GroupActions, RowHandlers } from './propCells'
import { openOnRowClick } from './rowClick'
import type { PropEnvelope } from './propApi'

export function PropCard({ e, h }: { e: PropEnvelope; h: RowHandlers }) {
  return (
    <div
      className="pt-mrow"
      {...entityRefProps('envelope', e.id, e.label, undefined, undefined, e.color)}
      onClick={openOnRowClick(e, h)}
    >
      <div className="pt-mrow-top">
        <ColorIcon icon="envelope" color={e.color} />
        <button
          type="button"
          className="pt-ename"
          aria-label={'Edit ' + e.label}
          onClick={() => h.onEdit(e.id)}
        >
          {e.label}
        </button>
        <CatsChip e={e} h={h} />
        <span className="pt-mrow-left">
          <span className="pt-mk">Left</span>
          <LeftCell e={e} />
        </span>
      </div>
      <CapBar e={e} />
      <div className="pt-mrow-sub">
        <span className="pt-mk">Cap</span>
        <CapCell e={e} h={h} />
        <span className="pt-mk">Activity</span>
        <ActivityFor e={e} h={h} />
      </div>
    </div>
  )
}

export function PropCardGroup({
  f,
  closed,
  onToggle,
  income,
  actions,
  h,
}: {
  f: GroupFigures
  closed: boolean
  onToggle: () => void
  income: number
  actions: GroupActions
  h: RowHandlers
}) {
  const [naming, setNaming] = useState(false)
  const { g, rows: list } = f
  const { ct, hasCaps, left } = groupFigures(list)
  return (
    <section
      className={'pt-mgroup' + (g.excluded ? ' pt-group--out' : '') + (closed ? ' closed' : '')}
    >
      <div className="pt-mghead">
        <div className="pt-mghead-top">
          {naming ? (
            <InlineEdit
              initial={g.name}
              onSave={(t) => {
                setNaming(false)
                if (t.trim() && t.trim() !== g.name) actions.onRename(g.id, t.trim())
              }}
              onCancel={() => setNaming(false)}
            />
          ) : (
            <button
              type="button"
              className="pt-mgtoggle"
              onClick={onToggle}
              aria-expanded={!closed}
            >
              <Chevron dir={closed ? 'right' : 'down'} size={13} />
              <i className="pt-gdot" style={{ background: f.color }} />
              <span className="pt-mgname">{g.name}</span>
              <span className="pl-gcount">{list.length}</span>
            </button>
          )}
          <span className="pt-mgshare">
            <GroupShare f={f} income={income} />
          </span>
          {!g.synced && (
            <button
              type="button"
              className="pl-gadd"
              title={'Add an envelope to ' + g.name}
              aria-label="Add envelope"
              onClick={() => actions.onAddEnvelope(g.id, g.name)}
            >
              <Plus />
            </button>
          )}
          <GroupKebab f={f} onRename={() => setNaming(true)} actions={actions} />
        </div>
        <div className="pt-mgstats">
          <span>
            <em>Cap</em>
            <b className="mask">{ct ? fBase(ct) : '—'}</b>
          </span>
          <span>
            <em>Activity</em>
            <b className="mask">{fBase(f.spent ? -f.spent : 0)}</b>
          </span>
          <span>
            <em>Left</em>
            <b className={'mask' + (hasCaps ? (left < 0 ? ' bad' : left > 0 ? ' good' : '') : '')}>
              {hasCaps ? fBase(left) : '—'}
            </b>
          </span>
        </div>
      </div>
      {!closed && list.length > 0 && (
        <div className="pt-mgbody">
          {list.map((e) => (
            <PropCard key={e.id} e={e} h={h} />
          ))}
        </div>
      )}
    </section>
  )
}
