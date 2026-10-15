import { useState } from 'react'
import { desktopClientBridge } from '../../../../platform/desktop'
import { useQueryCache } from '../../../../state/queryCache'
import { ErrorState, Loading } from '../../../ui'
import { Btn, Panel, row } from '../ds'

const CONNECTION_KEY = 'native:desktop-connection'
const fetchConnection = () => {
  const client = desktopClientBridge()
  return client
    ? client.getConnection()
    : Promise.reject(new Error('Desktop connection is unavailable'))
}

export function DesktopConnectionPanel() {
  const client = desktopClientBridge()
  const connection = useQueryCache(CONNECTION_KEY, fetchConnection, { enabled: !!client })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run(action: () => Promise<unknown>, failure: string) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  if (!client) return null
  const data = connection.data
  const displayError =
    error ?? (connection.error ? 'Could not load the connection. Try again.' : null)
  const server = data?.mode === 'server'
  return (
    <Panel
      title="Connection"
      sub={
        server
          ? 'Your book and scheduled work run on this server. Closing this app does not stop the server.'
          : undefined
      }
    >
      {connection.initialLoading && !data && <Loading label="Loading connection…" />}
      {displayError && (
        <ErrorState
          message={displayError}
          onRetry={() => void run(connection.refresh, 'Could not load the connection. Try again.')}
        />
      )}
      {data &&
        row(
          server ? 'Connected to server' : 'This computer',
          server ? <span style={{ overflowWrap: 'anywhere' }}>{data.serverUrl}</span> : null,
          <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
            {server && (
              <Btn
                disabled={busy || !data.serverUrl}
                onClick={() =>
                  void run(() => client.openBrowser(), 'Could not open your browser. Try again.')
                }
              >
                Open in browser
              </Btn>
            )}
            <Btn
              disabled={busy}
              onClick={() =>
                void run(
                  () => client.changeConnection(),
                  'Could not change the connection. Try again.',
                )
              }
            >
              Change connection
            </Btn>
          </div>,
        )}
    </Panel>
  )
}
