import { describe, expect, it } from 'vitest'
import type { AssetCategory, OwnedAsset } from '../../../types/assets'
import { registerItems, stackOf, STACK_AT } from './stacks'

const CATS: AssetCategory[] = [
  { key: 'property', label: 'Property', plural: 'Property', color: '#888', custom: false },
  {
    key: 'watch',
    label: 'Watch & jewellery',
    plural: 'Watches & jewellery',
    color: '#96f',
    custom: false,
  },
  { key: 'c_pokemon', label: 'Pokemon Card', plural: 'Pokemon Cards', color: '#e57', custom: true },
]

let n = 0
function asset(category: string, value: number, extra: Partial<OwnedAsset> = {}): OwnedAsset {
  n += 1
  return {
    id: `oa_${n}`,
    name: `${category} ${n}`,
    category,
    value,
    base_value: value,
    base_share_value: value,
    base_share_cost: null,
    share_pct: 100,
    ...extra,
  } as OwnedAsset
}

describe('stacks — the register of many things (owner, 2026-09-28)', () => {
  it('stacks a category from 4 assets; fewer keep their own cards', () => {
    expect(STACK_AT).toBe(4)
    const homes = [
      asset('property', 650_000),
      asset('property', 544_000),
      asset('property', 180_000),
    ]
    const cards = [1, 2, 3, 4, 5].map((i) => asset('c_pokemon', i * 100))
    const items = registerItems([...homes, ...cards], CATS)
    expect(items.filter((i) => i.kind === 'asset')).toHaveLength(3)
    const stacks = items.filter((i) => i.kind === 'stack')
    expect(stacks).toHaveLength(1)
    expect(stacks[0]!.kind === 'stack' && stacks[0]!.stack.category.plural).toBe('Pokemon Cards')
  })

  it('orders everything by its value in the base currency — stacks by their total', () => {
    const usdCondo = asset('property', 200_000, {
      currency: 'USD',
      base_value: 270_000,
    } as Partial<OwnedAsset>)
    const sgdFlat = asset('property', 250_000)
    const watches = [60_000, 30_000, 20_000, 10_000].map((v) => asset('watch', v))
    const order = registerItems([sgdFlat, ...watches, usdCondo], CATS).map((i) =>
      i.kind === 'asset' ? i.asset.name : `stack ${i.stack.category.key}`,
    )
    // 270k (the USD condo in base) > 250k > the watches' 120k total.
    expect(order).toEqual([usdCondo.name, sgdFlat.name, 'stack watch'])
  })

  it('totals your share in the base currency, and its gain over the items with a price', () => {
    const stack = stackOf(CATS[1]!, [
      asset('watch', 20_000, { base_share_value: 20_000, base_share_cost: 16_000 }),
      asset('watch', 10_000, { share_pct: 50, base_share_value: 5_000, base_share_cost: 6_000 }),
      asset('watch', 3_000, { base_share_value: 3_000, base_share_cost: null }),
      asset('watch', 1_000, { base_share_value: 1_000 }),
    ])
    expect(stack.total).toBe(29_000)
    // (20,000 + 5,000) against (16,000 + 6,000): +13.6%; the two without a price don't count.
    expect(stack.gainPct).toBeCloseTo(13.636, 2)
    expect(stack.assets.map((a) => a.value)).toEqual([20_000, 10_000, 3_000, 1_000])
  })

  it('shows the photo of its most valuable pictured item — else the most valuable, its icon', () => {
    // Owner 2026-09-28: "shouldn't we show the first item/most value photo here?"
    const top = asset('watch', 50_000)
    const pictured = asset('watch', 20_000, { photo_ref: 'att_b' })
    const cheaper = asset('watch', 5_000, { photo_ref: 'att_c' })
    const plain = asset('watch', 1_000)
    expect(stackOf(CATS[1]!, [plain, cheaper, top, pictured]).cover).toBe(pictured)
    const none = [1, 2, 3, 4].map((i) => asset('c_pokemon', i))
    expect(stackOf(CATS[2]!, none).cover).toBe(none[3])
  })

  it('has no gain when none of its items has a price', () => {
    const stack = stackOf(
      CATS[2]!,
      [1, 2, 3, 4].map((i) => asset('c_pokemon', i)),
    )
    expect(stack.gainPct).toBeNull()
  })
})
