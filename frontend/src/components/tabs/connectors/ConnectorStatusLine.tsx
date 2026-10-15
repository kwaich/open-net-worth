// The "Last fetched …" line every provider config section renders — replaces each file's own
// "Last synced {timeAgo} • N accounts linked • N imported" line (that count now lives in
// ConnectorAccountsList). See .claude/rfcs/connectors-as-data-sources-ui-spec.md §1.2.A.
import { Text, type TextTone } from '../../ui'
import { timeAgo } from './timeAgo'

const STATUS_PHRASE: Record<string, string> = {
  ok: 'up to date',
  not_configured: 'not set up yet',
  disabled: 'paused',
  auth_error: 'credentials need attention',
  rate_limited: 'rate limited by the provider',
  connect_error: "couldn't reach the provider",
  error: 'error',
}

const STATUS_TONE: Record<string, TextTone> = {
  ok: 'faint',
  not_configured: 'faint',
  disabled: 'faint',
  auth_error: 'bad',
  rate_limited: 'warn',
  connect_error: 'bad',
  error: 'bad',
}

export function ConnectorStatusLine({
  lastSyncedAt,
  lastStatus,
}: {
  lastSyncedAt: string | null
  lastStatus: string
}) {
  if (!lastSyncedAt) {
    return (
      <Text size="xs" tone="faint">
        Last fetched: never
      </Text>
    )
  }
  const phrase = STATUS_PHRASE[lastStatus] ?? lastStatus
  const tone = STATUS_TONE[lastStatus] ?? 'faint'
  return (
    <Text size="xs" tone={tone}>
      Last fetched {timeAgo(lastSyncedAt)} · {phrase}
    </Text>
  )
}
