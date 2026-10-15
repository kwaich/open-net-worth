import type {
  DesktopBridge,
  DesktopClientBridge,
  DesktopCommand,
  NativeFile,
} from '../../../desktop/src/contracts'
import { authFetch } from '../api/client'
import { invalidateQueryCache } from '../state/queryCache'
import { PRIMARY_TABS, type TabId } from '../tabs'
import { tabToSlug } from '../routeSlug'

declare global {
  interface Window {
    onwDesktop?: DesktopBridge
    onwDesktopClient?: DesktopClientBridge
  }
}

const queue: File[] = []
const listeners = new Set<() => void>()
const jobs = new WeakMap<File, { id: string; attachmentId: string }>()
let transactionRequested = false

export function desktopBridge(): DesktopBridge | undefined {
  return typeof window !== 'undefined' && window.onwDesktop?.version === 1
    ? window.onwDesktop
    : undefined
}

/** True inside the macOS desktop window, whose title bar is the app's own top strip. The shell
 *  stamps `data-desktop-chrome="mac"` on <html> before the page scripts run. */
export function macDesktopChrome(): boolean {
  return typeof document !== 'undefined' && document.documentElement.dataset.desktopChrome === 'mac'
}

export function desktopClientBridge(): DesktopClientBridge | undefined {
  return typeof window !== 'undefined' && window.onwDesktopClient?.version === 1
    ? window.onwDesktopClient
    : undefined
}

export function subscribeDesktopCommands(listener: (command: DesktopCommand) => void): () => void {
  const local = desktopBridge()
  const bridge = local ?? desktopClientBridge()
  if (!bridge) return () => {}
  return bridge.onCommand((command) => {
    if (!local && command.type === 'import-files') return
    listener(command)
  })
}

/** The route a native `go` command opens, or null for anything that is not a main screen. */
export function goCommandPath(tab: unknown): string | null {
  return (PRIMARY_TABS as readonly unknown[]).includes(tab) ? `/${tabToSlug(tab as TabId)}` : null
}

export function enqueueNativeImports(files: NativeFile[]): void {
  for (const file of files) {
    queue.push(new File([new Uint8Array(file.bytes).buffer], file.name, { type: file.type }))
  }
  listeners.forEach((listener) => listener())
}

export function nativeImportCount(): number {
  return queue.length
}
export function nativeActionCount(): number {
  return queue.length + Number(transactionRequested)
}

export function requestNativeTransaction(): void {
  transactionRequested = true
  listeners.forEach((listener) => listener())
}

export function takeNativeTransaction(): boolean {
  const pending = transactionRequested
  transactionRequested = false
  if (pending) listeners.forEach((listener) => listener())
  return pending
}

export function subscribeNativeImports(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function takeNativeImport(): File | null {
  const file = queue.shift() ?? null
  if (file) listeners.forEach((listener) => listener())
  return file
}

export async function queueWatchedImport(job: {
  id: string
  attachment_id: string
  filename: string
}): Promise<void> {
  const response = await authFetch(`/api/uploads/${encodeURIComponent(job.attachment_id)}/file`)
  if (!response.ok) throw new Error('Could not open this imported file. Try again.')
  const blob = await response.blob()
  const file = new File([blob], job.filename, { type: blob.type })
  jobs.set(file, { id: job.id, attachmentId: job.attachment_id })
  queue.push(file)
  listeners.forEach((listener) => listener())
}

export function nativeImportAttachmentId(file: File): string | undefined {
  return jobs.get(file)?.attachmentId
}

export async function markNativeImportReviewed(file: File): Promise<boolean> {
  const id = jobs.get(file)?.id
  if (!id) return true
  try {
    const response = await authFetch(`/api/desktop/imports/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: 'reviewed' }),
    })
    if (!response.ok) return false
    invalidateQueryCache('/api/desktop/')
    return true
  } catch {
    return false
  }
}

export function openExternal(url: string): Promise<void> {
  const bridge = desktopBridge()
  if (bridge) return bridge.openExternal(url)
  window.open(url, '_blank', 'noopener,noreferrer')
  return Promise.resolve()
}

export function desktopArtifactUrl(id: string, type: string): string | undefined {
  return desktopBridge()
    ? `onw-artifact://preview/${encodeURIComponent(id)}?type=${type === 'svg' ? 'svg' : 'html'}`
    : undefined
}
