// The per-provider "Accounts" list — self-fetches GET /api/connectors/{provider}/accounts (not
// a shared read: it's per-provider, one caller) and reads the app's own account list through
// the shared `useAccounts` hook (RFC frontend-server-state-consolidation — the accounts
// collection endpoint is a SHARED read; a bare direct fetch of it here would re-fork the 19th
// caller the consolidation removed, which `sharedReadsInventory.test.ts` catches). Each row
// links a provider account to an existing app account (or opens AccountModal to create one) or,
// once linked, offers Unlink. Region 1.D of .claude/rfcs/connectors-as-data-sources-ui-spec.md.
import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Stack, Cluster, Text, Button, Select, EmptyState, ErrorState, Loading } from '../../ui'
import { useAccounts, type AccountLike } from '../../../hooks/serverState'
import { fCur } from '../accounts/format'
import { fromApiAccount } from '../accounts/data'
import AccountModal from '../accounts/AccountModal'
import type {
  Account,
  ApiAccount,
  ApiExternalId,
  AccountCreate,
  AccountUpdate,
} from '../accounts/types'

// `AccountLike`'s generic constraint needs its own explicit index signature (a plain
// `ApiAccount` fails useAccounts<ApiAccount>() — "Index signature for type 'string' is missing"
// — even though every one of its fields is structurally compatible); `extends AccountLike`
// inherits that signature so this component still gets the fields it actually reads.
interface AppAccountRow extends AccountLike {
  id: string
  name: string
  institution?: string | null
  external_ids?: ApiExternalId[]
}

interface ProviderAccountRow {
  provider_account_id: string
  name: string
  mask: string
  currency: string
  balance: number | null
  linked_account_id: string | null
}

interface ConnectorAccountsResponse {
  // Untyped beyond string — an unmapped value (a provider status this component doesn't yet
  // know) falls through to the generic error copy below, never a raw status string on screen.
  status: string
  provider_label: string
  accounts: ProviderAccountRow[]
}

const KNOWN_NON_ERROR_STATUSES = new Set([
  'ok',
  'not_configured',
  'disabled',
  'auth_error',
  'rate_limited',
  'connect_error',
])

const CREATE_OPTION = { value: '__create__', label: '+ Create account…' }

export function ConnectorAccountsList({
  provider,
  providerLabel,
}: {
  provider: string
  providerLabel: string
}) {
  const [data, setData] = useState<ConnectorAccountsResponse | null>(null)
  const [loadErr, setLoadErr] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [actionErr, setActionErr] = useState<string | null>(null)
  const [creatingFor, setCreatingFor] = useState<ProviderAccountRow | null>(null)
  // The shared cached read — never a direct fetch of the bare endpoint here (see the file
  // banner). `refresh()` re-fires it after a link/unlink/create so this list's own account
  // options and every OTHER consumer of the shared cache pick up the change together.
  const { data: appAccountsData, refresh: refreshAppAccounts } = useAccounts<AppAccountRow>()
  const appAccounts = appAccountsData ?? []

  const load = useCallback(() => {
    setLoadErr(false)
    authFetch(`/api/connectors/${provider}/accounts`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((body: ConnectorAccountsResponse) => setData(body))
      .catch(() => setLoadErr(true))
  }, [provider])

  useEffect(load, [load])

  async function putExternalIds(
    accountId: string,
    externalIds: Array<{ source: string; ref: string; name: string; mask: string }>,
  ) {
    const res = await authFetch(`/api/accounts/${accountId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ external_ids: externalIds }),
    })
    if (!res.ok) throw new Error()
  }

  async function link(row: ProviderAccountRow, accountId: string) {
    const acct = appAccounts.find((a) => a.id === accountId)
    if (!acct) return
    setBusyId(row.provider_account_id)
    setActionErr(null)
    try {
      await putExternalIds(accountId, [
        ...(acct.external_ids ?? []),
        { source: provider, ref: row.provider_account_id, name: row.name, mask: row.mask },
      ])
      load()
      void refreshAppAccounts()
    } catch {
      setActionErr('Could not link that account — try again.')
    } finally {
      setBusyId(null)
    }
  }

  async function unlink(row: ProviderAccountRow) {
    if (!row.linked_account_id) return
    const acct = appAccounts.find((a) => a.id === row.linked_account_id)
    if (!acct) {
      // The app account list hasn't loaded yet — refuse rather than PUT a guessed-empty
      // external_ids array, which would wipe out every OTHER link this account holds.
      setActionErr('Still loading accounts — try again in a moment.')
      return
    }
    setBusyId(row.provider_account_id)
    setActionErr(null)
    try {
      await putExternalIds(
        row.linked_account_id,
        (acct.external_ids ?? []).filter(
          (e) => !(e.source === provider && e.ref === row.provider_account_id),
        ),
      )
      load()
      void refreshAppAccounts()
    } catch {
      setActionErr('Could not unlink that account — try again.')
    } finally {
      setBusyId(null)
    }
  }

  async function createAndLink(form: AccountCreate | AccountUpdate): Promise<Account> {
    const res = await authFetch('/api/accounts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    })
    if (!res.ok) throw new Error(await res.text())
    const raw: ApiAccount = await res.json()
    const acct = fromApiAccount(raw)
    // A brand-new account holds no external_ids yet — link directly rather than through
    // `link()`, which looks the account up in `appAccounts` state that hasn't been
    // refreshed with this just-created row yet.
    if (creatingFor) {
      try {
        await putExternalIds(acct.id, [
          {
            source: provider,
            ref: creatingFor.provider_account_id,
            name: creatingFor.name,
            mask: creatingFor.mask,
          },
        ])
      } catch {
        setActionErr('Account created, but linking it failed — link it manually below.')
      }
    }
    setCreatingFor(null)
    load()
    void refreshAppAccounts()
    return acct
  }

  function linkedName(accountId: string | null): string {
    if (!accountId) return ''
    return appAccounts.find((a) => a.id === accountId)?.name ?? 'that account'
  }

  const accountOptions = appAccounts.map((a) => ({
    value: a.id,
    label: `${a.name} (${a.institution || 'No institution'})`,
  }))

  return (
    <Stack gap="--space-3">
      <Text size="sm" tone="ink">
        Accounts
      </Text>
      {!data && !loadErr && <Loading label="Loading accounts…" />}
      {loadErr && (
        <ErrorState message="Couldn't load accounts for this connector." onRetry={load} />
      )}
      {data && data.status === 'ok' && data.accounts.length === 0 && (
        <EmptyState
          title="No accounts yet"
          body="Link a bank or brokerage account above, then check back here."
        />
      )}
      {data && data.status === 'not_configured' && (
        <EmptyState
          title="Not set up yet"
          body={`Add your ${providerLabel} credentials above to see its accounts here.`}
        />
      )}
      {data && data.status === 'disabled' && (
        <EmptyState
          title="Paused"
          body={`${providerLabel} is paused. Resume it above to see its accounts.`}
        />
      )}
      {data && data.status === 'auth_error' && (
        <ErrorState
          message={`${providerLabel} rejected the saved credentials. Check them above, then retry.`}
          onRetry={load}
        />
      )}
      {data && data.status === 'rate_limited' && (
        <ErrorState
          message={`${providerLabel} is rate-limiting requests right now. Try again in a bit.`}
          onRetry={load}
        />
      )}
      {data && data.status === 'connect_error' && (
        <ErrorState message={`Couldn't reach ${providerLabel}.`} onRetry={load} />
      )}
      {data && !KNOWN_NON_ERROR_STATUSES.has(data.status) && (
        <ErrorState message={`Couldn't load accounts for ${providerLabel}.`} onRetry={load} />
      )}
      {/* Independent of `status`: the backend unions in every already-linked account (by
          `Account.external_ids`) even when the provider itself is unreachable/paused/never
          configured (connector_fetch.provider_accounts — "a linked account never vanishes
          from the picker just because the live call couldn't run"). The status banners above
          explain why live options/balances may be missing; they must not also hide a link a
          user already made. */}
      {data && data.accounts.length > 0 && (
        <Stack gap="--space-2">
          {data.accounts.map((row) => (
            <Cluster
              key={row.provider_account_id}
              gap="--space-3"
              align="center"
              wrap
              justify="between"
            >
              <Text size="xs" tone="faint">
                {row.name} •{row.mask} · {row.currency}
                {row.balance != null
                  ? ` · ${fCur(row.balance, row.currency, { cents: true })}`
                  : ''}
              </Text>
              {row.linked_account_id ? (
                <Cluster gap="--space-2" align="center">
                  <Text size="xs" tone="ink">
                    Linked to "{linkedName(row.linked_account_id)}"
                  </Text>
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={busyId === row.provider_account_id}
                    onClick={() => void unlink(row)}
                  >
                    Unlink
                  </Button>
                </Cluster>
              ) : (
                <Select
                  aria-label={`Link ${row.name} to an account`}
                  placeholder="Link to an account…"
                  value=""
                  disabled={busyId === row.provider_account_id}
                  options={[...accountOptions, CREATE_OPTION]}
                  onChange={(e) => {
                    const value = e.target.value
                    if (value === CREATE_OPTION.value) setCreatingFor(row)
                    else if (value) void link(row, value)
                  }}
                />
              )}
            </Cluster>
          ))}
        </Stack>
      )}
      {actionErr && (
        <Text size="xs" tone="bad">
          {actionErr}
        </Text>
      )}
      <AccountModal
        open={creatingFor !== null}
        mode="add"
        institutions={
          [...new Set(appAccounts.map((a) => a.institution).filter(Boolean))].sort() as string[]
        }
        prefill={
          creatingFor
            ? {
                name: creatingFor.name,
                currency: creatingFor.currency,
                lastFour: creatingFor.mask,
                institution: providerLabel,
              }
            : undefined
        }
        onClose={() => setCreatingFor(null)}
        onSave={createAndLink}
      />
    </Stack>
  )
}
