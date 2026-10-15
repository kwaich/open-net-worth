// budgetLens/proportional/PropRows.tsx — the 50/30/20 table's groups and envelope rows. The groups
// are the book's Zero-Based groups (owner, 2026-09-26), so a row sits where it sits in Zero-Based,
// and both are drawn as Zero-Based draws them ("these things we can make the same style"): a
// group header with its grip, chevron, name, count, + and ⋮; a row with its status line and bar
// (read against the cap). Every cell is shared with the phone card (propCells.tsx / PropCards.tsx).
import { useState } from 'react'
import type { DragEvent } from 'react'
import { entityRefProps } from '../../../../../utils/mentionable'
import { ColorIcon, InlineEdit } from '../../budgetParts'
import { Chevron, Grip, Plus } from '../../icons'
import { fBase } from '../../planData'
import { PropCardGroup } from './PropCards'
import {
  ActivityFor,
  CapBar,
  CapCell,
  CapStatus,
  CatsChip,
  Dash,
  GroupKebab,
  GroupShare,
  LeftCell,
} from './propCells'
import type { GroupActions, GroupDnd, RowHandlers } from './propCells'
import { openOnRowClick } from './rowClick'
import { groupFigures } from './propMath'
import type { GroupFigures } from './propMath'
import type { PropEnvelope } from './propApi'

const GripIc = () => (
  <svg width="7" height="12" viewBox="0 0 7 12" fill="currentColor">
    <circle cx="1.6" cy="2" r="1.1" />
    <circle cx="5.4" cy="2" r="1.1" />
    <circle cx="1.6" cy="6" r="1.1" />
    <circle cx="5.4" cy="6" r="1.1" />
    <circle cx="1.6" cy="10" r="1.1" />
    <circle cx="5.4" cy="10" r="1.1" />
  </svg>
)

export function PropRow({
  e,
  locked,
  h,
  noDrop = false,
}: {
  e: PropEnvelope
  locked: boolean
  h: RowHandlers
  /** A row others can't be dropped beside — one in the Excluded section, which has no order. */
  noDrop?: boolean
}) {
  // Zero-Based's insertion line (plan.css .pl-crow.over-top / .over-bottom): where it lands.
  const line = h.overRow?.id === e.id ? ' over-' + h.overRow.edge : ''
  return (
    <div
      className={'pl-crow pl-trow pt-crow' + (h.dragging === e.id ? ' dragging' : '') + line}
      // Always draggable, for the GLOBAL mention drag (row → Lucius) — as Zero-Based's rows are;
      // reordering is locked-gated separately (a Commitments row keeps its place).
      draggable
      {...entityRefProps('envelope', e.id, e.label, undefined, undefined, e.color)}
      onDragStart={(ev: DragEvent) => {
        if (locked) return
        ev.dataTransfer.effectAllowed = 'move'
        ev.dataTransfer.setData('text/plain', e.id)
        h.onDragStart(e.id)
      }}
      onDragEnd={h.onDragEnd}
      {...(locked || noDrop
        ? {}
        : { onDragOver: h.onRowOver(e.id), onDrop: h.onRowDrop(e.id, e.group_id) })}
      onClick={openOnRowClick(e, h)}
    >
      <span className="pt-cname">
        {!locked && (
          <span className="pt-grip" aria-hidden="true">
            <GripIc />
          </span>
        )}
        <ColorIcon icon="envelope" color={e.color} />
        <span className="pl-cmain">
          <span className="pl-cline">
            {/* The name opens the editor — no separate Edit or Move buttons (owner, 2026-09-26:
                "no need to have these 2 buttons"; a desktop drags to move). */}
            <button
              type="button"
              className="pt-ename"
              aria-label={'Edit ' + e.label}
              title="Edit envelope"
              onClick={() => h.onEdit(e.id)}
            >
              {e.label}
            </button>
            <CatsChip e={e} h={h} />
            <CapStatus e={e} />
          </span>
          <CapBar e={e} />
        </span>
      </span>
      <span className="pl-c-num pt-capcell">
        <CapCell e={e} h={h} />
      </span>
      <span className="pl-c-num">
        <ActivityFor e={e} h={h} />
      </span>
      <span className="pl-c-num">
        <LeftCell e={e} />
      </span>
    </div>
  )
}

export function PropGroup({
  f,
  closed,
  onToggle,
  income,
  dragOver,
  dropProps,
  actions,
  gd,
  h,
}: {
  f: GroupFigures
  closed: boolean
  onToggle: () => void
  income: number
  dragOver: boolean
  dropProps: Record<string, (e: DragEvent) => void>
  actions: GroupActions
  gd: GroupDnd
  h: RowHandlers
}) {
  const [naming, setNaming] = useState(false)
  if (h.mobile)
    return (
      <PropCardGroup
        f={f}
        closed={closed}
        onToggle={onToggle}
        income={income}
        actions={actions}
        h={h}
      />
    )
  const { g, rows: list } = f
  const { ct, hasCaps, left } = groupFigures(list)
  const capOver = f.targetAmt !== null && ct > f.targetAmt
  const dragging = gd.dragging === g.id
  const over = gd.over === g.id && !dragging
  return (
    <div
      className={'pt-group' + (dragOver ? ' over' : '') + (g.excluded ? ' pt-group--out' : '')}
      {...(g.synced ? {} : dropProps)}
    >
      {/* Zero-Based's group header, piece for piece (budget.tsx): the whole row folds it. */}
      <div
        className={
          'pl-grow pl-trow pt-grow' + (over ? ' grow-over' : '') + (dragging ? ' dragging' : '')
        }
        onClick={onToggle}
        onDragOver={gd.onOver(g.id)}
        onDrop={gd.onDrop(g.id)}
        onDragEnd={gd.end}
      >
        <span className="pl-gname">
          {g.synced ? (
            <span className="pl-grip pl-grip--group" aria-hidden="true" />
          ) : (
            <button
              type="button"
              className="pl-grip pl-grip--group"
              draggable
              onDragStart={gd.start(g.id)}
              onClick={(e) => e.stopPropagation()}
              aria-label={'Drag group ' + g.name}
              title="Drag to reorder groups"
            >
              <Grip />
            </button>
          )}
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
            <span className="pl-gtoggle">
              <button
                type="button"
                className="pl-gtog-btn"
                onClick={(e) => {
                  e.stopPropagation()
                  onToggle()
                }}
                aria-expanded={!closed}
                aria-label={(closed ? 'Expand ' : 'Collapse ') + g.name}
              >
                <span className="pl-chevron">
                  <Chevron dir={closed ? 'right' : 'down'} size={13} />
                </span>
              </button>
              {/* The group's colour in the hero above — Zero-Based groups carry none. */}
              <i className="pt-gdot" style={{ background: f.color }} />
              {g.synced ? (
                <span className="pl-gname-btn" style={{ cursor: 'default' }}>
                  {g.name}
                </span>
              ) : (
                <button
                  type="button"
                  className="pl-gname-btn"
                  onClick={(e) => {
                    e.stopPropagation()
                    setNaming(true)
                  }}
                  title={'Rename ' + g.name}
                >
                  {g.name}
                </button>
              )}
            </span>
          )}
          <span className="pl-gcount">{list.length}</span>
          <GroupShare f={f} income={income} />
          {!g.synced && (
            <button
              type="button"
              className="pl-gadd"
              title={'Add an envelope to ' + g.name}
              aria-label="Add envelope"
              onClick={(e) => {
                e.stopPropagation()
                actions.onAddEnvelope(g.id, g.name)
              }}
            >
              <Plus />
            </button>
          )}
          <GroupKebab f={f} onRename={() => setNaming(true)} actions={actions} />
        </span>
        <span
          className={'pl-c-num pt-num strong' + (capOver ? ' warn' : '')}
          title={capOver ? `Caps add up to more than the ${g.target}% target` : ''}
        >
          {ct ? fBase(ct) : <Dash />}
        </span>
        {/* Signed like the rows' own Activity: spending is "−S$645.18". */}
        <span className={'pl-c-num pt-num strong' + (f.spent ? '' : ' dim')}>
          {fBase(f.spent ? -f.spent : 0)}
        </span>
        {!hasCaps ? (
          <span className="pl-c-num">
            <Dash />
          </span>
        ) : (
          <span
            className={
              'pl-c-num pt-num strong' + (left < 0 ? ' warn' : left > 0 ? ' good' : ' dim')
            }
          >
            {fBase(left)}
          </span>
        )}
      </div>
      {!closed && list.map((e) => <PropRow key={e.id} e={e} locked={g.synced} h={h} />)}
      {/* An empty group says nothing until something is being dragged — then it's the target. */}
      {!closed && !list.length && !g.synced && h.dragging && (
        <div className="pt-dropnote">Drop here</div>
      )}
    </div>
  )
}
