// Wording and rules that only apply when the app runs on a Private Cloud server (gateway mode).

export const TERMINAL_NOTICE =
  'This is your server. Tools you sign in to here are used under your own agreement with their provider.'

export const NO_LOCAL_MODELS_NOTE =
  "Local models (Ollama, LM Studio) aren't offered here: this app runs on your Private Cloud server, which can't reach a model running on your own computer."

/** A provider whose default address only exists on the machine running the app. */
export function isLocalOnlyProvider(entry: { key: string; base_url?: string }): boolean {
  return (
    entry.key === 'ollama' ||
    /^https?:\/\/(localhost|127\.\d+\.\d+\.\d+|\[::1\])/i.test(entry.base_url ?? '')
  )
}
