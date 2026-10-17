import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import type { DesktopSettings } from '../../../../../../desktop/src/contracts'
import { ErrorState, Loading } from '../../../ui'
import { Badge, Btn, Empty, Panel, Toggle, row } from '../ds'
import {
  DESKTOP_SETTINGS_KEY as SETTINGS_KEY,
  desktopBridge,
  desktopClientBridge,
  fetchDesktopSettings as fetchSettings,
  queueWatchedImport,
} from '../../../../platform/desktop'
import { DesktopConnectionPanel } from './DesktopConnectionPanel'
import { fetchJson } from '../../../../hooks/serverState'
import { setCachedQueryData, useQueryCache } from '../../../../state/queryCache'

interface ImportJob {
  id: string
  grant_id: string
  status: string
  attachment_id: string | null
  filename: string
  error?: string | null
}

const JOBS_KEY = '/api/desktop/imports'
const fetchJobs = () => fetchJson<{ jobs: ImportJob[] }>(JOBS_KEY)

export function DesktopSection() {
  const local = desktopBridge()
  if (!local && !desktopClientBridge()) return null
  return (
    <>
      <DesktopConnectionPanel />
      {local ? <LocalDesktopSection /> : <ServerActivitySettings />}
    </>
  )
}

function ServerActivitySettings() {
  const openActivitySettings = desktopClientBridge()?.openActivitySettings
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  if (!openActivitySettings) return null

  async function open() {
    setBusy(true)
    setError(null)
    try {
      await openActivitySettings!()
    } catch {
      setError('Could not open background and startup settings. Try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      title="Background and startup"
      sub="Your automations and Intelligence keep updating on your server, even when this app is closed."
    >
      {row(
        'Keep Open Net Worth ready',
        'Keep it close at hand and ready when you sign in.',
        <Btn disabled={busy} onClick={() => void open()}>
          Background and startup…
        </Btn>,
      )}
      {error && <ErrorState message={error} />}
    </Panel>
  )
}

function LocalDesktopSection() {
  const bridge = desktopBridge()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const imports = useQueryCache(JOBS_KEY, fetchJobs, { enabled: !!bridge, refetchInterval: 5000 })
  const preferences = useQueryCache(SETTINGS_KEY, fetchSettings, {
    enabled: !!bridge,
    refetchInterval: bridge ? 5000 : undefined,
  })
  const settings = preferences.data

  async function load() {
    if (!bridge) return
    try {
      await preferences.refresh()
      setError(null)
    } catch {
      setError('Could not load desktop settings. Try again.')
    }
  }
  async function mutate(action: () => Promise<DesktopSettings>) {
    setBusy(true)
    try {
      setCachedQueryData(SETTINGS_KEY, await action())
      setError(null)
      await imports.refresh()
    } catch {
      setError('Could not save this desktop setting. Try again.')
    } finally {
      setBusy(false)
    }
  }

  async function review(job: ImportJob) {
    if (!job.attachment_id) return
    setBusy(true)
    try {
      await queueWatchedImport({ ...job, attachment_id: job.attachment_id })
      await navigate({
        to: '/accounts',
        search: { account: undefined, txn: undefined, review: undefined },
      })
    } catch {
      setError('Could not open this file for review. Try again.')
    } finally {
      setBusy(false)
    }
  }

  async function chooseBook() {
    if (!bridge) return
    setBusy(true)
    setError(null)
    try {
      await bridge.chooseBookFolder()
    } catch {
      setError('Could not open this book folder. Try again.')
    } finally {
      setBusy(false)
    }
  }

  async function openBrowser() {
    if (!bridge) return
    setBusy(true)
    setError(null)
    try {
      await bridge.openBrowser()
    } catch {
      setError('Could not open your browser. Try again.')
    } finally {
      setBusy(false)
    }
  }

  if (!bridge) return null
  if (preferences.initialLoading && !error) return <Loading label="Loading desktop settings…" />
  const displayError =
    error ?? (preferences.error ? 'Could not load desktop settings. Try again.' : null)
  const jobs = imports.data?.jobs ?? []
  return (
    <>
      {displayError && <ErrorState message={displayError} onRetry={() => void load()} />}
      {settings && (
        <>
          <Panel
            title="Browser access"
            sub="Open the same book in your browser on this computer. Browser access follows Settings → Security."
          >
            {row(
              <span style={{ overflowWrap: 'anywhere' }}>
                {settings.browserUrl ?? 'Browser access is unavailable while the book opens.'}
              </span>,
              'Keep the desktop app running. Quit stops browser access. The address may change after a restart or when you open another book.',
              <Btn disabled={busy || !settings.browserUrl} onClick={() => void openBrowser()}>
                Open in browser
              </Btn>,
            )}
          </Panel>
          <Panel title="Background activity">
            {row(
              'Keep running when the window closes',
              'Keep your scheduled automations running and Intelligence fresh with news and alerts—even when you close the window.',
              <Toggle
                label="Keep running when the window closes"
                on={settings.background}
                disabled={busy}
                onChange={(on) => void mutate(() => bridge.updateSettings({ background: on }))}
              />,
            )}
            {row(
              'Start at login',
              'Let Lucius get back to work when you sign in, without remembering to open the app.',
              <Toggle
                label="Start at login"
                on={settings.launchAtLogin}
                disabled={busy}
                onChange={(on) => void mutate(() => bridge.updateSettings({ launchAtLogin: on }))}
              />,
            )}
            {row(
              'Keep computer awake',
              'Stop idle sleep so automations run on time. The display can still turn off.',
              <Toggle
                label="Keep computer awake"
                on={settings.keepAwake}
                disabled={busy}
                onChange={(on) => void mutate(() => bridge.updateSettings({ keepAwake: on }))}
              />,
            )}
          </Panel>
          <Panel title="Notifications">
            {row(
              'Show desktop notifications',
              'Notifications open the relevant review. Financial values and document contents stay out of notification previews. If alerts do not appear, allow Open Net Worth in your computer’s notification settings.',
              <Toggle
                label="Show desktop notifications"
                on={settings.notifications}
                disabled={busy}
                onChange={(on) => void mutate(() => bridge.updateSettings({ notifications: on }))}
              />,
            )}
          </Panel>
          <Panel
            title="Statement folders"
            sub="New files appear for review. You decide what gets booked."
            actions={
              <Btn disabled={busy} onClick={() => void mutate(() => bridge.chooseWatchedFolder())}>
                Choose folder…
              </Btn>
            }
          >
            {!settings.watchedFolders.length && (
              <Empty
                title="Add a statement folder"
                body="New files appear for review. You decide what gets booked."
              />
            )}
            {settings.watchedFolders.map((folder) => (
              <div key={folder.id}>
                {row(
                  <>
                    {folder.label}{' '}
                    <Badge tone={!folder.enabled ? 'bad' : folder.available ? 'good' : 'warn'}>
                      {!folder.enabled
                        ? 'Access revoked'
                        : folder.available
                          ? 'Watching'
                          : 'Unavailable'}
                    </Badge>
                  </>,
                  <span style={{ overflowWrap: 'anywhere' }}>{folder.location}</span>,
                  <Btn
                    disabled={busy || !folder.enabled}
                    onClick={() => void mutate(() => bridge.revokeWatchedFolder(folder.id))}
                  >
                    Remove access
                  </Btn>,
                )}
              </div>
            ))}
            {jobs
              .filter((job) => job.status !== 'reviewed')
              .map((job) => (
                <div key={job.id}>
                  {row(
                    job.filename,
                    job.status === 'failed'
                      ? 'Could not read file'
                      : job.status === 'ready'
                        ? null
                        : 'Reading…',
                    job.status === 'ready' && job.attachment_id ? (
                      <Btn size="xs" disabled={busy} onClick={() => void review(job)}>
                        Review
                      </Btn>
                    ) : null,
                  )}
                </div>
              ))}
            {Boolean(imports.error) && (
              <ErrorState
                message="Could not load folder imports."
                onRetry={() => void imports.refresh()}
              />
            )}
          </Panel>
          <Panel
            title="Your book"
            sub="Your book stays on this computer. Export a portable backup to keep another copy."
          >
            {row(
              'Book folder',
              <span style={{ overflowWrap: 'anywhere' }}>{settings.bookFolder}</span>,
              <Btn
                disabled={busy}
                onClick={() =>
                  void bridge
                    .saveExport()
                    .catch(() => setError('Could not export your book. Try again.'))
                }
              >
                Export backup…
              </Btn>,
            )}
            {row(
              'Open another book',
              'Open another book folder, or choose an empty folder for a new book.',
              <Btn disabled={busy} onClick={() => void chooseBook()}>
                Open another folder…
              </Btn>,
            )}
          </Panel>
        </>
      )}
    </>
  )
}
