// HoldingsFilterSheet — the Holdings toolbar's filter button. Account always; asset class only
// when a caller passes it (Holdings shows class as pills at every width, so it doesn't).
//
// A thin composition over the generic FilterSheet (which owns the icon trigger, the desktop
// popover and the phone bottom sheet). Kept as its own component so the Holdings call site
// and its tests read in Holdings' own vocabulary (class / account) rather than generic groups.

import { FilterSheet } from './FilterSheet'
import type { FilterOption } from './FilterSheet'

export type { FilterOption } from './FilterSheet'

interface Props {
  className?: string
  presentation?: 'popover' | 'sheet'
  classOptions?: FilterOption[]
  classValue?: string
  onClassChange?: (id: string) => void
  accountOptions: FilterOption[]
  accountValue: string
  onAccountChange: (id: string) => void
}

/** Anything other than "all classes + all accounts" counts as filtered. */
function isFiltered(classValue: string | undefined, accountValue: string): boolean {
  return (classValue ?? 'all') !== 'all' || accountValue !== ''
}

export function HoldingsFilterSheet({
  className,
  presentation = 'popover',
  classOptions,
  classValue,
  onClassChange,
  accountOptions,
  accountValue,
  onAccountChange,
}: Props) {
  return (
    <FilterSheet
      className={className}
      presentation={presentation}
      title="Filter holdings"
      active={isFiltered(classValue, accountValue)}
      groups={[
        ...(classOptions && onClassChange
          ? [
              {
                title: 'Type',
                options: classOptions,
                value: classValue ?? 'all',
                onChange: onClassChange,
              },
            ]
          : []),
        {
          title: 'Account',
          options: accountOptions,
          value: accountValue,
          onChange: onAccountChange,
        },
      ]}
    />
  )
}
