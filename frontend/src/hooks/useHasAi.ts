// Is a model actually configured on this instance?
//
// Needed in three places that must agree: both empty states (whether to offer "just tell
// Lucius") and the floating dock (whether "Ask Lucius" is a promise the app can keep). The dock
// used to offer a chat on an instance with no provider at all — a dead end for anyone who
// declined the model step.
//
// Deliberately narrower than useModels(): that hook fetches the whole model LIST for pickers.
// This answers one boolean, and re-answers it when the config changes so first run configuring
// a model updates the UI without a reload.

import { useEffect, useState } from 'react'
import { authFetch } from '../api/client'
import { MODEL_CONFIG_CHANGED_EVENT } from './useModels'

export interface HasAi {
  hasAi: boolean
  /** False until the first answer arrives — callers should not render a verdict before then. */
  loaded: boolean
}

async function readHasAi(): Promise<boolean> {
  try {
    const res = await authFetch('/api/model-config')
    if (!res.ok) return false
    const cfg = await res.json()
    // `provider` is only populated once default_model resolves to a real provider:model ref,
    // which is exactly the condition under which a turn can run.
    return Boolean((cfg?.provider || '').trim())
  } catch {
    return false
  }
}

export function useHasAi(): HasAi {
  const [state, setState] = useState<HasAi>({ hasAi: false, loaded: false })

  useEffect(() => {
    let alive = true
    const refresh = () => {
      void readHasAi().then((hasAi) => {
        if (alive) setState({ hasAi, loaded: true })
      })
    }
    refresh()
    window.addEventListener(MODEL_CONFIG_CHANGED_EVENT, refresh)
    return () => {
      alive = false
      window.removeEventListener(MODEL_CONFIG_CHANGED_EVENT, refresh)
    }
  }, [])

  return state
}
