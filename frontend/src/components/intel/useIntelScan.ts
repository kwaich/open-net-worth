import { useState } from 'react'
import { authFetch } from '../../api/client'
import { setCachedQueryData } from '../../state/queryCache'
import { INTEL_SIGNALS_KEY } from '../../hooks/useIntelStoryIndex'
import type { ScanState } from './ScanToast'
import type { StoryArc } from './storyMeta'

/** A manual intelligence scan: confirm (it spends web-search quota), run, then report how many
 *  signals it auto-resolved — or that it is still processing (a parked batch), or that it failed. */
export function useIntelScan(stories: StoryArc[], reloadWatchlist: () => Promise<unknown>) {
  const [scan, setScan] = useState<ScanState>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)

  async function run() {
    setConfirmOpen(false)
    setScan({ phase: 'scanning' })
    // Snapshot which signals were already auto-resolved so only the new ones are reported.
    const wasResolved = new Set(stories.filter((s) => s.resolved).map((s) => s.id))
    try {
      const scanResponse = await authFetch('/api/intel/scan', { method: 'POST' })
      if (!scanResponse.ok) throw new Error('Scan request failed')
      const scanResult = await scanResponse.json()
      // A parked batch: the analysis lands in the feed when it finishes, not now.
      if (scanResponse.status === 202 && scanResult?.pending === true) {
        setScan({
          phase: 'pending',
          msg: 'Analysis will appear in the feed when processing finishes.',
        })
        return
      }
      if (scanResult?.ok !== true) throw new Error('Scan did not complete')
      const [res] = await Promise.all([authFetch(INTEL_SIGNALS_KEY), reloadWatchlist()])
      if (!res.ok) throw new Error('Could not refresh signals')
      const next: StoryArc[] = await res.json()
      setCachedQueryData(INTEL_SIGNALS_KEY, next)
      const newlyResolved = next.filter((s) => s.resolved && !wasResolved.has(s.id)).length
      const msg = newlyResolved
        ? `Up to date • ${newlyResolved} signal${newlyResolved === 1 ? '' : 's'} auto-resolved.`
        : 'Up to date • nothing new, your feed is current.'
      setScan({ phase: 'done', msg })
      window.setTimeout(() => setScan((s) => (s?.phase === 'done' ? null : s)), 4200)
    } catch {
      setScan({
        phase: 'error',
        msg: 'Could not complete the scan. Check your connection and try again.',
      })
    }
  }

  return {
    scan,
    scanning: scan?.phase === 'scanning',
    dismiss: () => setScan(null),
    confirmOpen,
    askToScan: () => setConfirmOpen(true),
    cancel: () => setConfirmOpen(false),
    run,
  }
}
