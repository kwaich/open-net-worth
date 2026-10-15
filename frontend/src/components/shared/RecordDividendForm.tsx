import { useEffect, useState } from 'react'
import type { ComputedRow } from '../../types/portfolio'
import { recordIncome } from '../../api/portfolio'
import { toLocalISODate } from './DisposalForm'
import { ACCOUNTS_KEY, fetchJson } from '../../hooks/serverState'
import { fetchCachedQuery } from '../../state/queryCache'
import {
  Modal,
  FormField,
  NumberField,
  TextField,
  Select,
  Checkbox,
  Stack,
  Cluster,
  Button,
  ErrorState,
} from '../ui'

interface Props {
  row: ComputedRow
  onDone: () => void
  onClose: () => void
  // Optional default for the income type select. Defaults to 'dividend'.
  // Pass 'interest' for bond rows so the most common case is pre-selected.
  defaultKind?: 'dividend' | 'interest' | 'distribution'
}

interface AccountLite {
  id: string
  name: string
  kind: string
}

// Record a dividend / interest / distribution a holding paid (P2-INV2). A CASH dividend
// books ONE income transaction through the ledger choke point (no double-count); a DRP
// reinvestment acquires a lot and books no cash. ALL income math is the service's
// (POST /api/income → apply_income_change); this form only collects input.
export function RecordDividendForm({ row, onDone, onClose, defaultKind = 'dividend' }: Props) {
  const positionId = row.position_id || ''
  const ccy = row.currency || ''

  const [gross, setGross] = useState('')
  const [tax, setTax] = useState('')
  const [kind, setKind] = useState<'dividend' | 'interest' | 'distribution'>(defaultKind)
  const [paidAt, setPaidAt] = useState(() => toLocalISODate())
  const [isDrp, setIsDrp] = useState(false)
  const [drpQty, setDrpQty] = useState('')
  const [accountId, setAccountId] = useState('')
  const [accounts, setAccounts] = useState<AccountLite[]>([])
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetchCachedQuery<AccountLite[]>(ACCOUNTS_KEY, () => fetchJson<AccountLite[]>(ACCOUNTS_KEY))
      .then((data) => {
        if (!alive) return
        const cash = data.filter((a) => a.kind === 'asset')
        setAccounts(cash)
        // Default to the holding's own account if it's a cash account, else the first.
        const own = cash.find((a) => a.id === row.account_id)
        setAccountId(own?.id || cash[0]?.id || '')
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [row.account_id])

  const grossNum = parseFloat(gross) || 0
  const taxNum = parseFloat(tax) || 0
  const drpQtyNum = parseFloat(drpQty) || 0
  const net = Math.max(0, grossNum - taxNum)

  const canSubmit =
    !!positionId && grossNum > 0 && !submitting && (isDrp ? drpQtyNum > 0 : !!accountId)

  async function submit() {
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      await recordIncome({
        position_id: positionId,
        gross_amount: grossNum,
        tax_withheld: taxNum,
        currency: ccy || undefined,
        income_kind: kind,
        paid_at: paidAt,
        is_drp: isDrp,
        ...(isDrp ? { drp_quantity: drpQtyNum } : { account_id: accountId }),
      })
      onDone()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not record the income.')
    } finally {
      setSubmitting(false)
    }
  }

  const footer = (
    <Cluster gap="--space-3" justify="end">
      <Button variant="secondary" onClick={onClose} disabled={submitting}>
        Cancel
      </Button>
      <Button variant="primary" onClick={submit} disabled={!canSubmit} loading={submitting}>
        Record
      </Button>
    </Cluster>
  )

  return (
    <Modal
      open
      onClose={onClose}
      title={`Record income — ${row.label || row.symbol}`}
      size="md"
      footer={footer}
    >
      <Stack gap="--space-4">
        <FormField label="Type" htmlFor="rdf-kind">
          <Select
            id="rdf-kind"
            value={kind}
            onChange={(e) => setKind(e.target.value as typeof kind)}
            options={[
              { value: 'dividend', label: 'Dividend' },
              { value: 'interest', label: 'Interest' },
              { value: 'distribution', label: 'Distribution' },
            ]}
          />
        </FormField>

        <Cluster gap="--space-4" align="start">
          <FormField label={`Gross amount (${ccy})`} htmlFor="rdf-gross">
            <NumberField
              id="rdf-gross"
              inputMode="decimal"
              value={gross}
              onChange={(e) => setGross(e.target.value)}
            />
          </FormField>
          <FormField label={`Tax withheld (${ccy})`} htmlFor="rdf-tax">
            <NumberField
              id="rdf-tax"
              inputMode="decimal"
              value={tax}
              onChange={(e) => setTax(e.target.value)}
            />
          </FormField>
        </Cluster>

        <FormField label="Paid on" htmlFor="rdf-paid-at">
          <TextField
            id="rdf-paid-at"
            type="date"
            value={paidAt}
            onChange={(e) => setPaidAt(e.target.value)}
          />
        </FormField>

        <Checkbox
          id="rdf-drp"
          label="Reinvest (DRP)"
          checked={isDrp}
          onChange={(e) => setIsDrp(e.target.checked)}
        />

        {isDrp ? (
          <FormField label="Units reinvested" htmlFor="rdf-drp-qty">
            <NumberField
              id="rdf-drp-qty"
              inputMode="decimal"
              value={drpQty}
              onChange={(e) => setDrpQty(e.target.value)}
            />
          </FormField>
        ) : (
          <FormField label="Cash account" htmlFor="rdf-account">
            <Select
              id="rdf-account"
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              options={accounts.map((a) => ({ value: a.id, label: a.name }))}
            />
          </FormField>
        )}

        <p className="rdf-preview">
          {isDrp
            ? `Reinvests ${net.toLocaleString()} ${ccy} into a new lot (no cash booked).`
            : `Books ${net.toLocaleString()} ${ccy} (net of tax) as income.`}
        </p>

        {error && <ErrorState message={error} />}
      </Stack>
    </Modal>
  )
}
