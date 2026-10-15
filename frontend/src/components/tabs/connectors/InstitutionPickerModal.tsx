import { useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Stack, Cluster, Grid, Text, Badge } from '../../ui'
import { Btn, Modal } from './primitives'

// The unified "Find your bank" front-door (P3-LINK §3) — search-first: pick a country
// (+ optional name) → we federate the providers + region routing via
// GET /api/account-linking/institutions → the user picks a specific bank (where the provider
// has a per-bank list) OR a provider-level option → we route them to that provider's existing
// config form on its detail page. It NEVER dead-ends: a manual-import fallback row is always
// shown. This is a router + lens; it opens NO write path — the actual link/consent happens in
// the provider's own section.
//
// The SHELL is the Connectors redesign's own `Modal` (spec §9's pinned header/footer,
// body-only scroll) rather than `ui/Modal`, because this dialog is now opened from the
// redesigned tab and a second modal vocabulary inside one surface reads as a leftover.
// The SEARCH ROW follows it into that vocabulary (`rd-field` / `rd-input` / `Btn`): spec §9
// makes a control's height a fixed contract, and `ui/TextField` + `ui/Button` are padding-
// sized, so the row could never sit on one baseline while it mixed the two. The routing and
// the fallback below it are untouched — they are the good part.

interface Institution {
  id: string
  name: string
  country: string | null
  provider: string
  logo: string | null
}

interface ProviderOption {
  provider: string
  name: string
  region: string
  note: string
  per_bank_list: boolean
  configured: boolean
}

interface SearchResult {
  institutions: Institution[]
  providers: ProviderOption[]
  manual_import: boolean
}

// A connector id is the provider id for the bank aggregators (1:1).
const PROVIDER_TO_CONNECTOR: Record<string, string> = {
  plaid: 'plaid',
  simplefin: 'simplefin',
  enable_banking: 'enable_banking',
}

export function InstitutionPickerModal({
  open,
  onClose,
  onRouteToProvider,
}: {
  open: boolean
  onClose: () => void
  /** Close + open the resolved provider's Configure flow (its connector card). */
  onRouteToProvider: (connectorId: string, country?: string) => void
}) {
  const [country, setCountry] = useState('')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<SearchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Reset on (re)open so the picker always starts clean.
  useEffect(() => {
    if (open) {
      setCountry('')
      setQuery('')
      setResult(null)
      setError(null)
    }
  }, [open])

  async function search() {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams()
      if (country.trim()) params.set('country', country.trim())
      if (query.trim()) params.set('q', query.trim())
      const r = await authFetch(`/api/account-linking/institutions?${params.toString()}`)
      if (!r.ok) {
        setError('Could not search institutions.')
        return
      }
      setResult((await r.json()) as SearchResult)
    } catch {
      setError('Could not search institutions.')
    } finally {
      setLoading(false)
    }
  }

  function route(provider: string) {
    const connectorId = PROVIDER_TO_CONNECTOR[provider]
    if (connectorId) onRouteToProvider(connectorId, country.trim().toUpperCase() || undefined)
  }

  if (!open) return null

  return (
    <Modal
      title="Find your bank"
      onClose={onClose}
      size="lg"
      footer={<Btn onClick={onClose}>Close</Btn>}
    >
      <Stack gap="--space-5">
        <Text size="sm" tone="faint">
          Type your country (and optionally your bank's name) — we'll find the right provider to
          link it and take you to its setup. Can't auto-sync your bank? Email can often log the same
          activity straight from your bank's own notifications, or you can always import a
          statement.
        </Text>

        {/* One row, three controls (see `.cn-linkrow`). The help is a CHILD OF THE ROW, not of
            the country field: inside the field it made that column taller than its neighbour,
            which is what threw the labels, the inputs and the button onto three baselines. */}
        <div className="cn-linkrow">
          <div className="rd-field">
            <span className="rd-label" id="link-country-label">
              Country
            </span>
            <input
              id="link-country"
              className="rd-input"
              aria-labelledby="link-country-label"
              aria-describedby="link-country-help"
              value={country}
              placeholder="US"
              maxLength={2}
              onChange={(e) => setCountry(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === 'Enter' && search()}
            />
          </div>
          <div className="rd-field">
            <span className="rd-label" id="link-query-label">
              Bank name (optional)
            </span>
            <input
              id="link-query"
              className="rd-input"
              aria-labelledby="link-query-label"
              value={query}
              placeholder="Chase, Revolut, …"
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && search()}
            />
          </div>
          <span className="rd-help" id="link-country-help">
            2-letter code, e.g. US, GB, DE
          </span>
          <Btn onClick={search} disabled={loading} aria-busy={loading || undefined}>
            Search
          </Btn>
        </div>

        {error && (
          <Text size="sm" tone="bad">
            {error}
          </Text>
        )}

        {result && (
          <Stack gap="--space-5">
            {/* Specific banks (where the provider exposes a per-bank list). */}
            {result.institutions.length > 0 && (
              <Stack gap="--space-3">
                <Text size="xs" tone="faint" className="conn-dir__group">
                  Banks
                </Text>
                <Grid min={240} gap="--space-3">
                  {result.institutions.map((inst) => (
                    <button
                      key={`${inst.provider}:${inst.id}`}
                      type="button"
                      className="conn-dir-card"
                      onClick={() => route(inst.provider)}
                    >
                      <span className="conn-dir-card__body">
                        <Cluster gap="--space-2" align="center">
                          <Text size="sm" weight={600}>
                            {inst.name}
                          </Text>
                          <Badge tone="muted">via {inst.provider.replace(/_/g, ' ')}</Badge>
                        </Cluster>
                        {inst.country && (
                          <Text size="xs" tone="faint">
                            {inst.country}
                          </Text>
                        )}
                      </span>
                    </button>
                  ))}
                </Grid>
              </Stack>
            )}

            {/* Provider-level options (Plaid/SimpleFIN — bank chosen inside the hosted flow). */}
            <Stack gap="--space-3">
              <Text size="xs" tone="faint" className="conn-dir__group">
                Connect via a provider
              </Text>
              {result.providers.length === 0 ? (
                <Text size="sm" tone="faint">
                  No auto-sync provider covers this country yet — but Email is a real option here,
                  not just a fallback. Point Lucius at your bank or broker's own notification emails
                  and it logs the same activity; for some institutions that's actually richer than a
                  sync feed, not a lesser substitute for one.
                </Text>
              ) : (
                <Grid min={240} gap="--space-3">
                  {result.providers.map((p) => (
                    <button
                      key={p.provider}
                      type="button"
                      className="conn-dir-card"
                      onClick={() => route(p.provider)}
                    >
                      <span className="conn-dir-card__body">
                        <Cluster gap="--space-2" align="center">
                          <Text size="sm" weight={600}>
                            {p.name}
                          </Text>
                          <Badge tone="muted">{p.region}</Badge>
                          {p.configured && <Badge tone="pos">Configured</Badge>}
                        </Cluster>
                        <Text size="xs" tone="faint" className="conn-dir-card__desc">
                          {p.note}
                        </Text>
                      </span>
                    </button>
                  ))}
                </Grid>
              )}
            </Stack>

            {/* The always-present manual-import fallback so the door never dead-ends. */}
            {result.manual_import && (
              <Text size="xs" tone="faint">
                Don't see your bank? You can always import a statement (CSV / OFX / QIF) from
                Settings → Data.
              </Text>
            )}
          </Stack>
        )}
      </Stack>
    </Modal>
  )
}
