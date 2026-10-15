// The harness note that starts a first-run conversation so Lucius speaks first, with no
// human message to reply to yet. Shared so every place that reads a conversation's first
// message — rendering it, or naming the conversation from it — recognises the same string
// rather than each keeping its own copy that can drift. Mirrors `ai.tool_text.ONBOARDING_INIT`
// on the backend, which is the harness-side twin of this check.
export const ONBOARDING_INIT_SENTINEL = '__ONBOARDING_INIT__'

export function isOnboardingInit(text: string): boolean {
  return text.trim() === ONBOARDING_INIT_SENTINEL
}
