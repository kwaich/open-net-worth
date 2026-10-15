import { useEffect, type RefObject } from 'react'
import type { LuciusComposerHandle } from '../components/chat/lucius/Composer'
import { registerDraftSource } from '../utils/gatewayReauth'

/** Hook for a mounted composer surface: its live content is what a re-sign-in must keep. */
export function useReauthDraftSource(composerRef: RefObject<LuciusComposerHandle | null>): void {
  useEffect(
    () =>
      registerDraftSource('composer', () => {
        const current = composerRef.current
        return current && !current.isEmpty() ? current.serialize() : null
      }),
    [composerRef],
  )
}
