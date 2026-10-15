// budgetLens/OnboardHost.tsx — mounts 50/30/20's onboarding (proportional/Onboard.tsx) whenever
// useBudgetLens has it open, pre-filled with the Profile's monthly net income. Mounted once, in
// BudgetLensPane, beside the switch and compare modals.
import { useQueryCache } from '../../../../state/queryCache'
import { PROFILE_KEY, fetchEngineDraft, fetchProfile } from './api'
import { currentYm } from './period'
import { Onboard } from './proportional/Onboard'
import type { OnboardGroup } from './proportional/Onboard'
import { GROUPS_KEY, fetchGroups } from './proportional/envelopeOps'
import type { BudgetLensState } from './useBudgetLens'

export function OnboardHost({
  lens,
  onAskLucius,
}: {
  lens: BudgetLensState
  onAskLucius: (text: string) => void
}) {
  const profile = useQueryCache(PROFILE_KEY, fetchProfile, { enabled: lens.onboarding })
  // Each group's last-three-months spend — the setup proposes a share from it.
  const draft = useQueryCache(
    '/api/planning/engines/proportional/draft',
    () => fetchEngineDraft('proportional', currentYm()),
    { enabled: lens.onboarding },
  )
  // A new book has no income yet, so the draft is empty — the setup still lists every spending
  // group, at no history, so each can be given a share (QA 2026-09-28: a fresh book saw only
  // "Savings 100%").
  const groupsQ = useQueryCache(GROUPS_KEY, fetchGroups, { enabled: lens.onboarding })
  if (!lens.onboarding) return null
  // A beat's wait beats a blank income box the Profile would have filled.
  if (
    (profile.initialLoading && !profile.data) ||
    (draft.initialLoading && !draft.data) ||
    (groupsQ.initialLoading && !groupsQ.data)
  )
    return null
  const drafted = (draft.data?.proposed_state as { rows?: OnboardGroup[] } | null)?.rows ?? []
  const rows: OnboardGroup[] = drafted.length
    ? drafted
    : (groupsQ.data ?? [])
        .filter((g) => !g.synced)
        .map((g) => ({ group_id: g.id, name: g.name, avg_spend: 0 }))
  return (
    <Onboard
      profileIncome={Number(profile.data?.monthly_net_income) || 0}
      groups={rows}
      busy={lens.switching}
      error={lens.onboardError}
      onDone={lens.finishOnboarding}
      onLucius={() => {
        lens.cancelOnboarding()
        onAskLucius('Set up my 50/30/20 budget')
      }}
      onClose={lens.cancelOnboarding}
    />
  )
}
