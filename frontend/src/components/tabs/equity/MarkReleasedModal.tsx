import { useState } from 'react'
import type { PendingReleaseItem, ReleaseResult } from '../../../types/equity'
import { Modal, FormField, TextField, Stack, Cluster, Button, Text, ErrorState } from '../../ui'
import { authFetch } from '../../../api/client'
import { fCurrency, fDate, localIso } from '../../../utils/format'

interface Props {
  item: PendingReleaseItem
  onClose: () => void
  onDone: () => void
}

type ModalPhase = 'form' | 'success'

/** Confirms that a pending vest's shares have actually been DELIVERED.
 *  Deliberately asks for one thing — the date they landed. It moves shares only: no
 *  transaction is written and the lot keeps the vest-date basis, so there is nothing
 *  else for the operator to get wrong here. */
export function MarkReleasedModal({ item, onClose, onDone }: Props) {
  const recordsLegacyDateOnly = item.release_mode === 'record_only'
  const today = localIso()
  const [releaseDate, setReleaseDate] = useState(
    item.expected_release_date && item.expected_release_date <= today
      ? item.expected_release_date
      : today,
  )
  const [submitting, setSubmitting] = useState(false)
  const [phase, setPhase] = useState<ModalPhase>('form')
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [result, setResult] = useState<ReleaseResult | null>(null)

  // The vest date is the floor: shares cannot be delivered before they vest.
  const canSubmit =
    !submitting && releaseDate.length === 10 && releaseDate >= item.vest_date && phase === 'form'

  async function handleConfirm() {
    if (!canSubmit) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const res = await authFetch(`/api/equity/vest-events/${item.vest_event_id}/release`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ release_date: releaseDate }),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error((detail as { detail?: string }).detail || `HTTP ${res.status}`)
      }
      setResult(await res.json())
      setPhase('success')
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : 'Release failed. Please try again.')
      setSubmitting(false)
    }
  }

  const title = `Mark released — ${item.symbol} • ${fDate(item.vest_date, 'short', 'always')} vest`

  if (phase === 'success' && result) {
    return (
      <Modal
        open
        onClose={onDone}
        title={title}
        size="lg"
        closeOnBackdrop={false}
        dragToDismiss
        footer={
          <Button variant="primary" onClick={onDone}>
            Done
          </Button>
        }
      >
        <Stack gap="--space-5">
          <Text as="p" size="base" tone="faint">
            {recordsLegacyDateOnly ? 'Release recorded' : 'Shares delivered'}
          </Text>
          <Text as="p" size="base">
            {recordsLegacyDateOnly ? (
              `${item.units.toLocaleString()} units keep their existing vest-date lot; the delivery date is now recorded.`
            ) : (
              <>
                {item.units.toLocaleString()} units are now sellable holdings, at a cost basis of{' '}
                {fCurrency(item.fmv_per_unit, item.fmv_currency)} per unit from the{' '}
                {fDate(item.vest_date, 'short', 'always')} vest.
              </>
            )}
          </Text>
          <Text as="p" size="sm" tone="faint">
            {recordsLegacyDateOnly
              ? 'No shares or money were added — the existing lot and any earlier transactions remain unchanged.'
              : 'No cash transaction was written — this release moves shares and preserves their vest-date cost basis.'}
          </Text>
        </Stack>
      </Modal>
    )
  }

  const footer = (
    <Cluster gap="--space-3" justify="between">
      <Button variant="secondary" onClick={onClose} disabled={submitting}>
        Cancel
      </Button>
      <Button variant="primary" onClick={handleConfirm} disabled={!canSubmit}>
        {submitting ? 'Saving…' : 'Confirm release'}
      </Button>
    </Cluster>
  )

  return (
    <Modal
      open
      onClose={onClose}
      title={title}
      size="lg"
      closeOnBackdrop={false}
      dragToDismiss
      footer={footer}
    >
      <Stack gap="--space-5">
        <Text as="p" size="sm" tone="faint">
          {item.units.toLocaleString()} units vested on {fDate(item.vest_date, 'short', 'always')}
          {item.expected_release_date
            ? ` • expected ${fDate(item.expected_release_date, 'short', 'always')}`
            : ''}
          .{' '}
          {recordsLegacyDateOnly ? (
            'This legacy vest already has its vest-date lot. Marking it released records when the shares landed without adding shares or changing money.'
          ) : (
            <>
              Marking them released moves them into your sellable holdings. Nothing about the money
              changes — the lot keeps the vest date and the{' '}
              {fCurrency(item.fmv_per_unit, item.fmv_currency)} vest-date price.
            </>
          )}
        </Text>

        <FormField
          label="Release date"
          htmlFor="release-date"
          help={`The day the shares landed in the account. Cannot be before the ${fDate(item.vest_date, 'short', 'always')} vest date.`}
        >
          <TextField
            id="release-date"
            type="date"
            min={item.vest_date}
            value={releaseDate}
            onChange={(e) => setReleaseDate(e.target.value)}
          />
        </FormField>

        {releaseDate.length === 10 && releaseDate < item.vest_date && (
          <Text as="p" size="sm" tone="bad">
            Shares can't be delivered before they vest.
          </Text>
        )}

        {submitError && <ErrorState message={submitError} />}
      </Stack>
    </Modal>
  )
}
