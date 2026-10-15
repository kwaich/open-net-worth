/**
 * registerFit.ts — which register columns fit the room the register actually has.
 *
 * The register is a fixed-pixel grid, so on a laptop (or with the Lucius panel open beside it)
 * its columns outrun the container and the rows scroll sideways. Instead of letting that happen
 * first, the register gives up the least valuable columns for as long as the window is narrow:
 * Memo (almost always empty), then Source (a row needing review then keeps a dot beside its
 * amount instead of the Review pill that lives in that column), and squeezes the rest toward
 * their resize minimums, always leaving the Description column room to read. Only when even that
 * does not fit does the grid overflow (`overflow`), and the Amount column pins to the right edge.
 * Nothing here is persisted: widening the window brings every column the user chose back.
 */

import type { RegCol } from './Register'

/** The row grid's horizontal padding (18px a side) and column gap — keep in step with `.acct-tr`. */
const ROW_PADDING = 36
const COLUMN_GAP = 10
/** Description is the column worth defending: the bank string you came to read. */
export const DESCRIPTION_COMFORT = 140
export const DESCRIPTION_FLOOR = 72

export interface RegisterFit {
  cols: RegCol[]
  widths: Record<string, number>
  /** True when even the squeezed grid is wider than the container. */
  overflow: boolean
  /** True when the Source column (home of the Review pill) was set aside. */
  sourceShed: boolean
}

const minOf = (c: RegCol): number => c.min ?? c.w

/** The widths that make `cols` fit `available` with the description at `descriptionPx`, squeezing
 *  every other column toward its minimum in proportion to its slack; null when they cannot. */
function squeeze(
  cols: RegCol[],
  widths: Record<string, number>,
  available: number,
  descriptionPx: number,
): Record<string, number> | null {
  const fixed = cols.filter((c) => c.key !== 'description')
  const wanted = fixed.reduce((sum, c) => sum + (widths[c.key] ?? c.w), 0)
  const hasDescription = fixed.length < cols.length
  const deficit = wanted + (hasDescription ? descriptionPx : 0) - available
  if (deficit <= 0) return widths
  const slack = (c: RegCol) => Math.max(0, (widths[c.key] ?? c.w) - minOf(c))
  const capacity = fixed.reduce((sum, c) => sum + slack(c), 0)
  if (capacity < deficit) return null
  const out = { ...widths }
  for (const c of fixed)
    out[c.key] = Math.round((widths[c.key] ?? c.w) - (slack(c) * deficit) / capacity)
  return out
}

export function fitRegisterColumns(
  cols: RegCol[],
  widths: Record<string, number>,
  containerWidth: number,
): RegisterFit {
  // Unmeasured (first paint, no layout engine): take the user's columns as they are.
  if (!(containerWidth > 0)) return { cols, widths, overflow: false, sourceShed: false }
  const without = (...keys: string[]) => cols.filter((c) => !keys.includes(c.key))
  const withoutMemo = without('memo')
  const bare = without('memo', 'source')
  const sourceShed = (list: RegCol[]) =>
    cols.some((c) => c.key === 'source') && !list.some((c) => c.key === 'source')
  const room = (list: RegCol[]) => containerWidth - ROW_PADDING - COLUMN_GAP * (list.length - 1)
  // Each step gives up a little more: comfortable description first, then a cramped one (every
  // column at its minimum) before a column is dropped, so Memo goes before Source does.
  const steps: [RegCol[], number][] = [
    [cols, DESCRIPTION_COMFORT],
    [withoutMemo, DESCRIPTION_COMFORT],
    [withoutMemo, DESCRIPTION_FLOOR],
    [bare, DESCRIPTION_COMFORT],
    [bare, DESCRIPTION_FLOOR],
  ]
  for (const [list, descriptionPx] of steps) {
    const fitted = squeeze(list, widths, room(list), descriptionPx)
    if (fitted) return { cols: list, widths: fitted, overflow: false, sourceShed: sourceShed(list) }
  }
  const floor = Object.fromEntries(bare.map((c) => [c.key, minOf(c)]))
  return { cols: bare, widths: floor, overflow: true, sourceShed: sourceShed(bare) }
}
