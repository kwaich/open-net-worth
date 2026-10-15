import { useEffect, useState } from 'react'
import { authFetch } from '../api/client'
export const MEMORY_SETTINGS_CHANGED_EVENT = 'onw:memory-settings-changed'

// Fetches GET /api/memory/settings once on mount; re-fetches when the
// MEMORY_SETTINGS_CHANGED_EVENT fires (Settings saves dispatch it). Used by
// AgentEditor and AgentDetailModal to gate per-agent memory controls when
// the global memory master switch is OFF.

export interface UseGlobalMemoryEnabled {
  globalMemoryOff: boolean
  loaded: boolean
}

export function useGlobalMemoryEnabled(): UseGlobalMemoryEnabled {
  const [globalMemoryOff, setGlobalMemoryOff] = useState(false)
  const [loaded, setLoaded] = useState(false)

  function fetch() {
    authFetch('/api/memory/settings')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data != null) {
          setGlobalMemoryOff(data.enabled === false)
        }
        setLoaded(true)
      })
      .catch(() => {
        setLoaded(true)
      })
  }

  useEffect(() => {
    void Promise.resolve().then(fetch)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    function onChanged() {
      void Promise.resolve().then(fetch)
    }
    window.addEventListener(MEMORY_SETTINGS_CHANGED_EVENT, onChanged)
    return () => window.removeEventListener(MEMORY_SETTINGS_CHANGED_EVENT, onChanged)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { globalMemoryOff, loaded }
}
