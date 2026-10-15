import { useEffect, useState } from 'react'
import {
  Modal,
  FormField,
  NumberField,
  TextField,
  Select,
  Stack,
  Cluster,
  Button,
  ErrorState,
} from '../../ui'
import { closeOption } from '../../../api/options'
import { fDate } from '../../../utils/format'
import type { OptionPosition } from './useOptionsView'
import { ACCOUNTS_KEY, fetchJson } from '../../../hooks/serverState'
import { fetchCachedQuery } from '../../../state/queryCache'

interface Props {
  position: OptionPosition
  onClose: () => void
  onDone: () => void
}

interface AccountLite {
  id: string
  name: string
  kind: string
  archived?: boolean
}

function toLocalISODate(): string {
  const dt = new Date()
  return `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-${String(dt.getDate()).padStart(2, '0')}`
}

export function CloseOptionModal({ position, onClose, onDone }: Props) {
  const [accounts, setAccounts] = useState<AccountLite[]>([])
  const [closedAt, setClosedAt] = useState(toLocalISODate())
  const [pricePerShare, setPricePerShare] = useState('')
  const [walletId, setWalletId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    fetchCachedQuery<AccountLite[]>(ACCOUNTS_KEY, () => fetchJson<AccountLite[]>(ACCOUNTS_KEY))
      .then((rows) => setAccounts(rows.filter((a) => a.kind === 'asset' && !a.archived)))
      .catch(() => setAccounts([]))
  }, [])

  const isShort = position.side === 'short'
  const contractLine = `${isShort ? '−' : '+'}${position.contracts} ${position.underlying_symbol} ${position.option_type} @ ${position.strike.toLocaleString()} • exp ${fDate(position.expiry, 'short', 'always')}`

  async function submit() {
    const pps = pricePerShare === '' ? undefined : Number(pricePerShare)
    if (pps !== undefined && (!Number.isFinite(pps) || pps < 0)) {
      setError('Close price per share cannot be negative.')
      return
    }
    if (pps !== undefined && pps > 0 && !walletId) {
      setError('Pick the wallet the cash leg should hit, or clear the close price.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await closeOption(position.id, {
        closed_at: closedAt || undefined,
        close_premium_per_share: pps,
        premium_account_id: walletId || undefined,
      })
      onDone()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not close the option.')
      setSubmitting(false)
    }
  }

  const footer = (
    <Cluster gap="--space-3" justify="between">
      <Button variant="ghost" onClick={onClose} disabled={submitting}>
        Cancel
      </Button>
      <Button variant="primary" onClick={() => void submit()} loading={submitting}>
        Close position
      </Button>
    </Cluster>
  )

  return (
    <Modal open onClose={onClose} title="Close option" size="md" footer={footer}>
      <Stack gap="--space-4">
        <p className="opt-close-line">{contractLine}</p>

        <FormField label="Close date" htmlFor="opt-close-date">
          <TextField
            id="opt-close-date"
            type="date"
            value={closedAt}
            onChange={(e) => setClosedAt(e.target.value)}
            data-autofocus
          />
        </FormField>

        <FormField
          label={isShort ? 'Buyback price / share' : 'Sale price / share'}
          htmlFor="opt-close-pps"
          help={
            isShort
              ? 'Optional: buy-to-close cost per share — books an expense from the wallet below.'
              : 'Optional: sell-to-close proceeds per share — books income to the wallet below.'
          }
        >
          <NumberField
            id="opt-close-pps"
            min={0}
            step="any"
            value={pricePerShare}
            onChange={(e) => setPricePerShare(e.target.value)}
          />
        </FormField>

        <FormField label="Book cash leg to" htmlFor="opt-close-wallet">
          <Select
            id="opt-close-wallet"
            value={walletId}
            onChange={(e) => setWalletId(e.target.value)}
            options={[
              { value: '', label: "Don't book a cash leg" },
              ...accounts.map((a) => ({ value: a.id, label: a.name })),
            ]}
          />
        </FormField>

        {error && <ErrorState message={error} />}
      </Stack>
    </Modal>
  )
}
