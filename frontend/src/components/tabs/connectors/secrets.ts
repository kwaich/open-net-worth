import { authFetch } from '../../../api/client'

/**
 * The connector-secret read-back seam: `GET /api/connectors/{id}/secret/{field}`.
 *
 * ONE endpoint for every family, so a form does not need to know which of ten per-provider
 * routers owns its credential. Auth-gated like every other route and deliberately absent
 * from the list/detail payloads — `GET /api/connectors` still answers `has_key` / `has_token`
 * and never a value, which is what keeps a credential out of caches, proxy logs and
 * screenshots. See `backend/services/connector_secrets.py` for the operator/model boundary.
 *
 * Rejects when nothing is saved (404 — e.g. a web-search key that lives only in the
 * environment) or the request fails; `useSecretReveal` turns that into the field's error
 * caption rather than a thrown error the form has to handle.
 */
export async function revealConnectorSecret(connectorId: string, field: string): Promise<string> {
  const res = await authFetch(
    `/api/connectors/${encodeURIComponent(connectorId)}/secret/${encodeURIComponent(field)}`,
  )
  if (!res.ok) throw new Error(`reveal failed: ${res.status}`)
  const body = (await res.json()) as { value?: string }
  if (!body?.value) throw new Error('reveal returned no value')
  return body.value
}
