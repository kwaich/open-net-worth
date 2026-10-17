import { useState } from 'react'
import { Info, Sun } from 'lucide-react'
import {
  DESKTOP_SETTINGS_KEY,
  desktopBridge,
  fetchDesktopSettings,
} from '../../../platform/desktop'
import { setCachedQueryData, useQueryCache } from '../../../state/queryCache'
import { Switch } from '../../ui'

/** Desktop (local book) only: automations run on this computer, so they stop while it sleeps. */
export function KeepAwakeBanner() {
  const bridge = desktopBridge()
  const settings = useQueryCache(DESKTOP_SETTINGS_KEY, fetchDesktopSettings, { enabled: !!bridge })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(false)
  if (!bridge || !settings.data) return null

  async function change(on: boolean) {
    setBusy(true)
    setError(false)
    try {
      setCachedQueryData(DESKTOP_SETTINGS_KEY, await bridge!.updateSettings({ keepAwake: on }))
    } catch {
      setError(true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="a3-awake" data-od-id="automations-keep-awake">
      <Info className="a3-awake-ic" size={15} strokeWidth={1.7} aria-hidden="true" />
      <span className="a3-awake-text">
        {error
          ? 'Could not change Keep awake. Try again.'
          : 'Automations only run while this computer is awake.'}
      </span>
      <span className="a3-awake-label" aria-hidden="true">
        <Sun className="a3-awake-ic" size={15} strokeWidth={1.7} />
        Keep awake
      </span>
      <Switch
        className="a3-awake-switch"
        aria-label="Keep computer awake"
        checked={settings.data.keepAwake}
        disabled={busy}
        onChange={(event) => void change(event.target.checked)}
      />
    </div>
  )
}
