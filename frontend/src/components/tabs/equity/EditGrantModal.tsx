import { useEffect, useState } from 'react'
import type { Grant, GrantUpdate, TaxEvent } from '../../../types/equity'
import { Modal, FormField, TextField, Select, Stack, Cluster, Button, ErrorState } from '../../ui'
import { authFetch } from '../../../api/client'
import { ACCOUNTS_KEY, fetchJson } from '../../../hooks/serverState'
import { fetchCachedQuery } from '../../../state/queryCache'

interface Props {
  grant: Grant
  onClose: () => void
  onDone: () => void
}

interface AccountLite {
  id: string
  name: string
  kind: string
}

const NEW_ACCOUNT_SENTINEL = '__new__'

/** Edits the mutable fields of an existing grant (instrument, type, and total_units are
 *  fixed at create time). Mirrors AddGrantModal's Advanced-section fields — everything
 *  the PATCH /api/equity/grants/{id} choke accepts. */
export function EditGrantModal({ grant, onClose, onDone }: Props) {
  const [grantDate, setGrantDate] = useState(grant.grant_date)
  const [grantPricePerUnit, setGrantPricePerUnit] = useState<number | null>(
    grant.grant_price_per_unit,
  )
  const [taxEvent, setTaxEvent] = useState<TaxEvent>(grant.tax_event)
  const [releaseRequired, setReleaseRequired] = useState(grant.release_required)
  const [note, setNote] = useState(grant.note)
  const [accountId, setAccountId] = useState(grant.account_id ?? '')
  const [newAccountName, setNewAccountName] = useState('')

  const [accounts, setAccounts] = useState<AccountLite[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetchCachedQuery<AccountLite[]>(ACCOUNTS_KEY, () => fetchJson<AccountLite[]>(ACCOUNTS_KEY))
      .then((data) => {
        if (!alive) return
        setAccounts(data.filter((a) => a.kind === 'asset'))
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  const accountChosen =
    (accountId !== '' && accountId !== NEW_ACCOUNT_SENTINEL) ||
    (accountId === NEW_ACCOUNT_SENTINEL && newAccountName.trim().length > 0)

  const isValid = grantDate.length === 10 && accountChosen

  async function handleSave() {
    if (!isValid || submitting) return
    setSubmitting(true)
    setError(null)

    const accountField: { account_id?: string; account_name?: string } =
      accountId === NEW_ACCOUNT_SENTINEL
        ? { account_name: newAccountName.trim() }
        : accountId !== grant.account_id
          ? { account_id: accountId }
          : {}

    const body: GrantUpdate = {
      grant_date: grantDate,
      tax_event: taxEvent,
      release_required: releaseRequired,
      note,
      ...accountField,
      ...(grantPricePerUnit != null ? { grant_price_per_unit: grantPricePerUnit } : {}),
    }

    try {
      const res = await authFetch(`/api/equity/grants/${grant.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}))
        throw new Error((detail as { detail?: string }).detail || `HTTP ${res.status}`)
      }
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save grant.')
      setSubmitting(false)
    }
  }

  const footer = (
    <Cluster gap="--space-3" justify="between">
      <Button variant="secondary" onClick={onClose} disabled={submitting}>
        Cancel
      </Button>
      <Button variant="primary" onClick={handleSave} disabled={!isValid || submitting}>
        {submitting ? 'Saving…' : 'Save changes'}
      </Button>
    </Cluster>
  )

  return (
    <Modal open onClose={onClose} title={`Edit ${grant.symbol} grant`} size="lg" footer={footer}>
      <Stack gap="--space-4">
        <FormField label="Grant date" htmlFor="edit-grant-date" required>
          <TextField
            id="edit-grant-date"
            type="date"
            value={grantDate}
            onChange={(e) => setGrantDate(e.target.value)}
          />
        </FormField>

        <FormField
          label="Price at grant (per unit)"
          htmlFor="edit-grant-price-per-unit"
          help="Optional — for reference and since-grant performance. Not used for tax."
        >
          <TextField
            id="edit-grant-price-per-unit"
            type="number"
            value={grantPricePerUnit ?? ''}
            min={0}
            step="any"
            onChange={(e) => {
              const v = parseFloat(e.target.value)
              setGrantPricePerUnit(isNaN(v) ? null : v)
            }}
          />
        </FormField>

        <FormField label="Tax event" htmlFor="edit-grant-tax-event" required>
          <Select
            id="edit-grant-tax-event"
            value={taxEvent}
            onChange={(e) => setTaxEvent(e.target.value as TaxEvent)}
            options={[
              { value: 'at_vest', label: 'Tax at vest — Singapore / US / Germany' },
              { value: 'deferred', label: 'Tax deferred — Vietnam and others' },
            ]}
          />
        </FormField>

        <FormField
          label="Share delivery"
          htmlFor="edit-grant-release-required"
          help="Choose delayed when your plan hands the shares over some time AFTER the vest date — a broker settlement lag, a blackout window, or country delivery rules. Vested units then count in your net worth but stay unsellable until you mark them released. Only affects vests recorded AFTER this change — history already on the books is untouched."
        >
          <Select
            id="edit-grant-release-required"
            value={releaseRequired ? 'delayed' : 'at_vest'}
            onChange={(e) => setReleaseRequired(e.target.value === 'delayed')}
            options={[
              { value: 'at_vest', label: 'Sellable at vest — the usual case' },
              { value: 'delayed', label: 'Delivered later — released after vest' },
            ]}
          />
        </FormField>

        <FormField
          label="Account"
          htmlFor="edit-grant-account"
          required
          help="Custodian or brokerage account where unvested units and vest transactions are tracked."
        >
          <Select
            id="edit-grant-account"
            value={accountId}
            onChange={(e) => {
              setAccountId(e.target.value)
              if (e.target.value !== NEW_ACCOUNT_SENTINEL) setNewAccountName('')
            }}
            options={[
              { value: '', label: 'Select an account…' },
              ...accounts.map((a) => ({ value: a.id, label: a.name })),
              { value: NEW_ACCOUNT_SENTINEL, label: '+ New account…' },
            ]}
          />
          {accountId === NEW_ACCOUNT_SENTINEL && (
            <TextField
              id="edit-grant-new-account-name"
              placeholder="Account name (e.g. JP Morgan)"
              value={newAccountName}
              onChange={(e) => setNewAccountName(e.target.value)}
              style={{ marginTop: 'var(--space-2)' }}
            />
          )}
        </FormField>

        <FormField label="Note" htmlFor="edit-grant-note">
          <TextField id="edit-grant-note" value={note} onChange={(e) => setNote(e.target.value)} />
        </FormField>

        {error && <ErrorState message={error} />}
      </Stack>
    </Modal>
  )
}
