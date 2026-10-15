// "How much did you pay?" (a policy: "How much have you paid in?") — the create wizard's optional
// step 4, reused verbatim by the asset page's Purchase editor. Controlled: the caller owns the
// value, the errors and the save.
import { useId } from 'react'
import { fNum, getCurrencySymbol } from '../../../utils/format'
import { DateField, FormField, MoneyField, Stack, Text } from '../../ui'
import { purchaseCopy } from './assetCategoryRules'
import type { PurchaseErrors, PurchaseValue } from './assetForms'

export type { PurchaseErrors, PurchaseValue } from './assetForms'

export interface PurchaseStepProps {
  category: string
  value: PurchaseValue
  /** One field's change — the owner merges it into its newest value (`withPurchase`). */
  onChange(patch: Partial<PurchaseValue>): void
  /** The currency the price is typed in. */
  currency: string
  /** The latest date that may be picked — today, as a local ISO date. */
  maxDate: string
  /** An existing asset's Purchase being edited — the intro drops "Optional". */
  editing?: boolean
  /** A money field's text can't be read (true) or can again (false) — its owner must not save. */
  onUnreadable?(field: string, unreadable: boolean): void
  errors: PurchaseErrors
}

export function PurchaseStep({
  category,
  value,
  onChange,
  currency,
  maxDate,
  errors,
  editing = false,
  onUnreadable,
}: PurchaseStepProps) {
  const id = useId()
  const copy = purchaseCopy(category)
  return (
    <Stack gap="--space-6">
      <Text as="p" size="sm" tone="muted">
        {editing ? copy.editIntro : copy.intro}
      </Text>
      <div className="as-form">
        <FormField label={copy.costLabel} htmlFor={`${id}-cost`} error={errors.cost}>
          <MoneyField
            id={`${id}-cost`}
            autoFocus
            invalid={!!errors.cost}
            allowEmpty
            prefix={getCurrencySymbol(currency)}
            value={value.cost}
            formattedValue={value.cost == null ? '' : fNum(value.cost)}
            onValueChange={(cost) => onChange({ cost })}
            onInvalidChange={(bad) => onUnreadable?.('cost', bad)}
          />
        </FormField>
        <FormField label={copy.dateLabel} htmlFor={`${id}-date`} error={errors.cost_date}>
          <DateField
            id={`${id}-date`}
            invalid={!!errors.cost_date}
            max={maxDate}
            value={value.costDate}
            onChange={(e) => onChange({ costDate: e.target.value })}
          />
        </FormField>
      </div>
    </Stack>
  )
}
