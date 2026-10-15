import { desktopBridge, desktopClientBridge } from '../platform/desktop'

// "Report a problem" is one entry point for every surface that offers it (Settings → Help, the error
// boundary, the boot-unreachable screen, a failed restore or provider check). Inside the desktop app
// the native dialog owns the report, because its logs and its recovery pages live in the main
// process; in a browser the page's own dialog opens (ReportProblemDialog, mounted once at the root).

export interface ReportRequest {
  /** The error the surface caught, shown in the preview for the user to keep or edit. */
  error?: string
}

let request: ReportRequest | null = null
const listeners = new Set<() => void>()

function publish(next: ReportRequest | null): void {
  request = next
  listeners.forEach((listener) => listener())
}

export function openReportProblem(trigger: ReportRequest = {}): void {
  const bridge = desktopBridge() ?? desktopClientBridge()
  if (bridge?.reportProblem) {
    // A refusal (the app is mid-operation) still leaves the user a way to report.
    void bridge.reportProblem(trigger.error).catch(() => publish({ ...trigger }))
    return
  }
  publish({ ...trigger })
}

export function closeReportProblem(): void {
  publish(null)
}

export function subscribeReportProblem(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function currentReportRequest(): ReportRequest | null {
  return request
}
