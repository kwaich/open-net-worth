// "Name and value" — the create wizard's step 2: the two things net worth needs.
import { useId } from 'react'
import { fNum, getCurrencySymbol } from '../../../utils/format'
import { FormField, MoneyField, Stack, Text, TextField } from '../../ui'
import { TEXT_CAPS } from './api'
import { namePlaceholder, valueHelp, valueLabel } from './assetCategoryRules'
import type { NameValueErrors } from './assetForms'

export interface NameValueStepProps {
  category: string
  /** The category's own guidance (e.g. where gold ETFs belong), when it has some. */
  hint?: string
  name: string
  value: number | null
  /** The currency the value is typed in. */
  currency: string
  errors: NameValueErrors
  onName(name: string): void
  onValue(value: number | null): void
  /** A money field's text can't be read (true) or can again (false) — its owner must not save. */
  onUnreadable?(field: string, unreadable: boolean): void
}

export function NameValueStep({
  category,
  hint,
  name,
  value,
  currency,
  errors,
  onName,
  onValue,
  onUnreadable,
}: NameValueStepProps) {
  const id = useId()
  return (
    <Stack gap="--space-6">
      {/* The category's own guidance leads the step, as step 4's intro does. */}
      {hint && (
        <Text as="p" size="sm" tone="muted">
          {hint}
        </Text>
      )}
      <FormField label="Name" htmlFor={`${id}-name`} required error={errors.name}>
        <TextField
          id={`${id}-name`}
          autoFocus
          invalid={!!errors.name}
          value={name}
          maxLength={TEXT_CAPS.name}
          placeholder={namePlaceholder(category)}
          onChange={(e) => onName(e.target.value)}
        />
      </FormField>
      <FormField
        label={valueLabel(category)}
        htmlFor={`${id}-value`}
        required
        help={valueHelp(category)}
        error={errors.value}
      >
        <MoneyField
          id={`${id}-value`}
          allowEmpty
          invalid={!!errors.value}
          prefix={getCurrencySymbol(currency)}
          value={value}
          formattedValue={value == null ? '' : fNum(value)}
          onValueChange={onValue}
          onInvalidChange={(bad) => onUnreadable?.('value', bad)}
        />
      </FormField>
    </Stack>
  )
}
