// budgetLens/proportional/propDnd.ts — drag an envelope within its group, as on the Zero-Based
// table (a line shows where it lands), or onto another group (it lands there, or at the bottom
// when dropped on the group itself). The groups ARE Zero-Based's (owner, 2026-09-26), so every drop
// is Zero-Based's own reorder or move — one order, the same on both screens.
import { useState } from 'react'
import type { DragEvent } from 'react'
import { setCachedQueryData } from '../../../../../state/queryCache'
import { moveEnvelope, reorderInGroup } from './envelopeOps'
import type { PropMonth } from './propApi'

type Row = PropMonth['envelopes'][number]
export type DropEdge = 'top' | 'bottom'
/** The row a dragged envelope is over, and which half — the line above or below it. */
export interface RowDrop {
  id: string
  edge: DropEdge
}

/** `rows` with `id` lifted out and put back in `groupId`: beside `target` (above or below it), or
 *  after that group's last row when there is no target. The table groups rows by group in list
 *  order, so this list is the new on-screen order. */
export function placeRow(rows: Row[], id: string, groupId: string, target: RowDrop | null) {
  const moving = rows.find((r) => r.id === id)
  if (!moving) return rows
  const rest = rows.filter((r) => r.id !== id)
  let at: number
  if (target) {
    const t = rest.findIndex((r) => r.id === target.id)
    at = t < 0 ? rest.length : t + (target.edge === 'bottom' ? 1 : 0)
  } else {
    const last = rest.map((r) => r.group_id).lastIndexOf(groupId)
    at = last < 0 ? rest.length : last + 1
  }
  return [...rest.slice(0, at), { ...moving, group_id: groupId }, ...rest.slice(at)]
}

/** Show the move at once, then save it: Zero-Based's reorder within a group, or its move (at the
 *  landing position) into another. */
export function placeAndSave(
  key: string,
  rows: Row[],
  id: string,
  groupId: string,
  target: RowDrop | null,
  onError: (e: unknown) => void,
) {
  const moving = rows.find((r) => r.id === id)
  if (!moving || target?.id === id) return
  const next = placeRow(rows, id, groupId, target)
  setCachedQueryData<PropMonth>(key, (prev) => (prev ? { ...prev, envelopes: next } : prev!))
  const inGroup = next.filter((r) => r.group_id === groupId).map((r) => r.id)
  const save =
    moving.group_id === groupId
      ? reorderInGroup(groupId, inGroup)
      : moveEnvelope(id, groupId, inGroup.indexOf(id))
  save.catch(onError)
}

type Place = (id: string, groupId: string, target: RowDrop | null) => void

/** The drag state, plus `bind(place)` for the row and group drop handlers. */
export function usePropDnd() {
  const [drag, setDrag] = useState<string | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const [overRow, setOverRow] = useState<RowDrop | null>(null)
  const end = () => {
    setDrag(null)
    setOver(null)
    setOverRow(null)
  }
  const bind = (place: Place) => ({
    rowOver: (id: string) => (e: DragEvent) => {
      if (!drag) return
      e.preventDefault()
      e.stopPropagation()
      if (over) setOver(null)
      if (id === drag) return setOverRow(null)
      const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
      const edge: DropEdge = e.clientY - r.top < r.height / 2 ? 'top' : 'bottom'
      setOverRow((o) => (o && o.id === id && o.edge === edge ? o : { id, edge }))
    },
    rowDrop: (id: string, groupId: string) => (e: DragEvent) => {
      if (!drag) return
      e.preventDefault()
      e.stopPropagation()
      if (id !== drag) place(drag, groupId, overRow?.id === id ? overRow : { id, edge: 'bottom' })
      end()
    },
    groupProps: (groupId: string) => ({
      onDragOver: (e: DragEvent) => {
        if (!drag) return
        e.preventDefault()
        if (overRow) setOverRow(null)
        if (over !== groupId) setOver(groupId)
      },
      onDragLeave: (e: DragEvent) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node))
          setOver((o) => (o === groupId ? null : o))
      },
      onDrop: (e: DragEvent) => {
        e.preventDefault()
        if (drag) place(drag, groupId, null)
        end()
      },
    }),
  })
  return { drag, over, overRow, start: setDrag, end, bind }
}
