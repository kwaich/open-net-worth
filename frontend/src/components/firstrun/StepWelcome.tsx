// First run, screen 1 — what this is, where the data lives, and the currency you think in.
//
// Base currency rides here rather than earning its own screen: it is one prefilled dropdown,
// and it CANNOT move to Lucius (design spec §5.1/§5.3) because it must be true before the
// first money row is written — the historical-rate lock means a `base_amount` is trusted only
// while its `base_currency` matches the current base. A user who declines AI would otherwise
// never be asked at all and would silently get USD.

import { useState } from 'react'
import { Button, Stack, Text } from '../ui'
import { desktopBridge } from '../../platform/desktop'
// The app's own currency control — searchable by code or name, keyboard-walkable, and
// already carrying a curated 154-currency list. A plain <select> of every ICU code was an
// unusable wall that also offered defunct units (XDR, the 1964-2022 leone).
import { CurrencyDialog } from './CurrencyDialog'

export interface StepWelcomeProps {
  currency: string
  onCurrencyChange: (code: string) => void
  dataDir: string | null
  /** Which rung answered: "env" (ONW_ROOT/DATA_DIR), "pointer", or "default". Telling someone
   *  to set a variable they have plainly already set is worse than saying nothing. */
  dataSource?: string | null
  /** What is already at `dataDir`, if anything — so the screen can say whether this run
   *  ADOPTS an existing book or starts an empty one, instead of silently doing either. */
  dataLocation?: {
    has_book: boolean
    accounts: number | null
    transactions: number | null
    writable: boolean
  } | null
  /** Re-read what is at the chosen folder after it changes, so the adopt-vs-empty line below
   *  reflects the folder now selected rather than the one the screen opened on. */
  onLocationChanged?: () => void
  onNext: () => void
  /** Restore a backup into the chosen folder instead of starting a new book. */
  onRestore?: () => void
  busy?: boolean
  /** Behind an authenticating proxy the host fixes the data location — nothing to state. */
  gatewayMode?: boolean
}

const MONO = 'ui-monospace, SFMono-Regular, Menlo, monospace'

/** "USD" -> "USD — US Dollar", from the runtime's own ICU names. */
function currencyLabel(code: string): string {
  try {
    const name = new Intl.DisplayNames(undefined, { type: 'currency' }).of(code)
    return name && name !== code ? `${code} — ${name}` : code
  } catch {
    return code
  }
}

export function StepWelcome({
  currency,
  onCurrencyChange,
  dataDir,
  dataSource,
  dataLocation,
  onNext,
  onRestore,
  busy,
  gatewayMode = false,
}: StepWelcomeProps) {
  const [editingCurrency, setEditingCurrency] = useState(false)
  const bridge = desktopBridge()

  return (
    <Stack gap="--space-7">
      {!bridge && (
        <Text as="p" tone="muted" className="firstrun-lead">
          {gatewayMode
            ? 'Accounts, investments, spending and net worth, all in one place.'
            : 'Accounts, investments, spending and net worth. Stored on your machine.'}
        </Text>
      )}

      <div className="firstrun-rows">
        {!bridge && !gatewayMode && dataDir && (
          <div className="firstrun-row">
            <span className="firstrun-row__label">Working folder</span>
            <span className="firstrun-row__value">
              <span style={{ fontFamily: MONO, overflowWrap: 'anywhere' }}>{dataDir}</span>
              {dataLocation?.has_book ? (
                <Text as="span" size="xs" tone="muted" className="firstrun-row__note">
                  Already holds {(dataLocation.accounts ?? 0).toLocaleString()} accounts
                  {dataLocation.transactions
                    ? ` • ${dataLocation.transactions.toLocaleString()} transactions`
                    : ''}
                  . This book will be opened as it is, not replaced.
                </Text>
              ) : (
                <Text as="span" size="xs" tone="faint" className="firstrun-row__note">
                  Everything lives here — your book, backups, cache and logs.
                  {dataSource === 'env' ? (
                    <>
                      {' '}
                      This location is set by <code>ONW_ROOT</code>.
                    </>
                  ) : (
                    <>
                      {' '}
                      To use a different folder, set <code>ONW_ROOT</code> before starting the app.
                    </>
                  )}
                </Text>
              )}
              {dataLocation && !dataLocation.writable && (
                <Text as="span" size="xs" tone="bad" className="firstrun-row__note">
                  This folder is not writable.
                </Text>
              )}
            </span>
          </div>
        )}

        <div className="firstrun-row">
          <span className="firstrun-row__label">App currency</span>
          <span className="firstrun-row__value">
            <span>{currencyLabel(currency)}</span>
            {editingCurrency && (
              <CurrencyDialog
                value={currency}
                onClose={() => setEditingCurrency(false)}
                onPick={(c) => {
                  onCurrencyChange(c)
                  setEditingCurrency(false)
                }}
              />
            )}
            <Text as="span" size="xs" tone="faint" className="firstrun-row__note">
              What your totals are shown in. Accounts can be in any currency — they convert into
              this one.
            </Text>
          </span>
          {/* ALWAYS rendered. It used to hide itself while the inline picker was open, and
              closing that picker without choosing left the row with no way back into it. */}
          <button
            type="button"
            className="firstrun-row__action"
            aria-label="Change app currency"
            disabled={busy}
            onClick={() => setEditingCurrency(true)}
          >
            Change
          </button>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-8)' }}>
        <Button variant="primary" loading={busy} onClick={onNext}>
          Continue
        </Button>
        {onRestore && (
          <Button variant="ghost" disabled={busy} onClick={onRestore}>
            I have a backup to restore
          </Button>
        )}
      </div>
    </Stack>
  )
}
