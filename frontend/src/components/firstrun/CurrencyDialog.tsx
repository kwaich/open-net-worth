// The base-currency chooser for first run — the shared searchable picker, filled with the
// app's curated currency list. See PickDialog for why this is a modal and not a dropdown.

import { useMemo } from 'react'
import { PickDialog } from './PickDialog'
import { CURRENCY_OPTIONS } from './currencies'

export interface CurrencyDialogProps {
  value: string
  onPick: (code: string) => void
  onClose: () => void
}

export function CurrencyDialog({ value, onPick, onClose }: CurrencyDialogProps) {
  const options = useMemo(
    () =>
      CURRENCY_OPTIONS.map((o) => {
        const [code = '', ...rest] = o.label.split(' — ')
        return { value: o.value, code, name: rest.join(' — ') }
      }),
    [],
  )

  return (
    <PickDialog
      title="App currency"
      subtitle="What your totals are shown in. Accounts can be in any currency."
      searchPlaceholder="Search by code or name…"
      options={options}
      value={value}
      onPick={onPick}
      onClose={onClose}
      emptyLabel="No currency matches “{q}”."
    />
  )
}
