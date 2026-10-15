// The register of many things (owner 2026-09-28: "a bit messy when users might have a lot of
// things"). A category with STACK_AT or more assets stacks into ONE card — its total, its gain,
// its most valuable items — and opens as a compact list; fewer keep their own cards, since a
// house or a car deserves one. Every figure is the server's base-currency one (`base_value`,
// `base_share_value`, `base_share_cost`), so sums and order hold across currencies.
import type { AssetCategory, OwnedAsset } from '../../../types/assets'
import { catOf } from './assetsData'

export const STACK_AT = 4

export interface AssetStack {
  category: AssetCategory
  /** Most valuable first. */
  assets: OwnedAsset[]
  /** Whose photo stands for the stack: the most valuable item with one — else the most
   *  valuable, whose thumb is the category's icon. */
  cover: OwnedAsset
  /** Your share of every item, in the base currency — what the category adds to net worth. */
  total: number
  /** Against what was paid, over the items that have a purchase price; null with none. */
  gainPct: number | null
}

export type RegisterItem =
  | { kind: 'asset'; asset: OwnedAsset }
  | { kind: 'stack'; stack: AssetStack }

/** What the register orders an asset by: its whole value in the base currency. */
export function orderValue(a: OwnedAsset): number {
  return a.base_value ?? a.value ?? 0
}

export function byValue(list: OwnedAsset[]): OwnedAsset[] {
  return [...list].sort((a, b) => orderValue(b) - orderValue(a))
}

export function stackOf(category: AssetCategory, assets: OwnedAsset[]): AssetStack {
  let total = 0
  let pricedValue = 0
  let cost = 0
  for (const a of assets) {
    const value = a.base_share_value ?? 0
    total += value
    if (a.base_share_cost != null) {
      pricedValue += value
      cost += a.base_share_cost
    }
  }
  const ordered = byValue(assets)
  return {
    category,
    assets: ordered,
    cover: ordered.find((a) => a.photo_ref) ?? ordered[0]!,
    total: Math.round(total * 100) / 100,
    gainPct: cost > 0 ? ((pricedValue - cost) / cost) * 100 : null,
  }
}

/** The register's assets, most valuable first — a category of STACK_AT or more as one stack,
 *  placed by its total. */
export function registerItems(list: OwnedAsset[], cats: AssetCategory[]): RegisterItem[] {
  const groups = new Map<string, OwnedAsset[]>()
  for (const a of list) groups.set(a.category, [...(groups.get(a.category) ?? []), a])
  const ranked: { value: number; item: RegisterItem }[] = []
  for (const [key, assets] of groups) {
    if (assets.length >= STACK_AT) {
      const stack = stackOf(catOf(cats, key), assets)
      ranked.push({ value: stack.total, item: { kind: 'stack', stack } })
    } else {
      for (const asset of assets)
        ranked.push({ value: orderValue(asset), item: { kind: 'asset', asset } })
    }
  }
  return ranked.sort((x, y) => y.value - x.value).map((x) => x.item)
}
