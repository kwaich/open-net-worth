// The empty Assets page is the populated page with nothing in it yet: the same grid and card
// shapes, each card an empty slot that adds the thing it names. Account classes come first,
// as they do once filled, because their money arrives from Accounts rather than from here.
import type { AssetCategory } from '../../../types/assets'
import { Icons } from './icons'

interface Slot {
  key: string
  icon: string
  name: string
  hint: string
  color: string
  /** Accessible name; the visible label is just "Add". */
  label: string
  onPick: () => void
}

const ACCOUNT_SLOTS = [
  {
    key: 'cash',
    type: 'cash',
    name: 'Cash & Savings',
    hint: 'Bank accounts, fixed deposits',
    color: 'var(--cat-cash)',
    label: 'Add bank account',
  },
  {
    key: 'investments',
    type: 'investment',
    name: 'Investments',
    hint: 'Brokerage, funds, crypto',
    color: 'var(--cat-invest)',
    label: 'Add brokerage account',
  },
  {
    key: 'retirement',
    type: 'retirement',
    name: 'Retirement',
    hint: 'CPF, 401(k), pension',
    color: 'var(--cat-retirement)',
    label: 'Add retirement account',
  },
  {
    key: 'card',
    type: 'credit',
    name: 'Credit cards',
    hint: 'What you owe',
    color: 'var(--contingent)',
    label: 'Add credit card',
  },
]

const ASSET_HINTS: Record<string, string> = {
  property: 'Home, apartment, land',
  vehicle: 'Car, motorbike, boat',
  watch: 'Watches, rings, jewellery',
  art: 'Paintings, wine, collectibles',
  metal: 'Gold and silver bars, coins',
  policy: 'Whole life, endowment, ILP',
}

// The tour lights the first slot of each kind.
const TOUR_TARGETS: Record<string, string> = {
  cash: 'assets-accounts',
  property: 'assets-things',
}

interface EmptyPreviewProps {
  cats: AssetCategory[]
  onAddAccount: (accountType: string) => void
  /** Null opens the wizard on its first step, every kind on offer. */
  onAddAsset: (category: string | null) => void
  /** Off for the populated page's "Add more", where accounts already have their own cards. */
  accounts?: boolean
}

export function EmptyPreview({
  cats,
  onAddAccount,
  onAddAsset,
  accounts = true,
}: EmptyPreviewProps) {
  const slots: Slot[] = [
    ...(accounts ? ACCOUNT_SLOTS : []).map((a) => ({
      ...a,
      icon: a.key,
      onPick: () => onAddAccount(a.type),
    })),
    ...cats
      .filter((c) => ASSET_HINTS[c.key])
      .map((c) => ({
        key: c.key,
        icon: c.key,
        name: c.label,
        hint: ASSET_HINTS[c.key]!,
        color: c.color,
        label: `Add ${c.label.toLowerCase()}`,
        onPick: () => onAddAsset(c.key),
      })),
    // The tiles list the common kinds; this one says the list isn't the limit. It opens the
    // wizard at its first step, where every kind (and making your own) is on offer.
    {
      key: 'other',
      icon: 'other',
      name: 'Other',
      hint: 'Custom',
      color: 'var(--faint)',
      label: 'Add another kind of asset',
      onPick: () => onAddAsset(null),
    },
  ]
  return (
    <div className="as-tiles">
      {slots.map((s) => (
        <button
          key={s.key}
          type="button"
          className="as-tile"
          aria-label={s.label}
          data-tour={TOUR_TARGETS[s.key]}
          onClick={s.onPick}
        >
          <span
            className="as-tile-thumb"
            style={{
              background: `color-mix(in srgb, ${s.color} 15%, var(--bg-2))`,
              borderColor: `color-mix(in srgb, ${s.color} 32%, var(--line))`,
              color: s.color,
            }}
          >
            {(Icons[s.icon] ?? Icons.other)!({ width: 18, height: 18 })}
          </span>
          <span className="as-tile-id">
            <span className="as-tile-nm">{s.name}</span>
            <span className="as-tile-ex">{s.hint}</span>
          </span>
          <span className="as-tile-plus" aria-hidden="true">
            {Icons.plus!({ width: 15, height: 15 })}
          </span>
        </button>
      ))}
    </div>
  )
}
