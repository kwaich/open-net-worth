// The ONE FIRE-settings editor model, shared by the Plan tab's modal and the Settings →
// Profile mirror's modal.
//
// The handoff is explicit: "Do not fork this modal per tab. If Plan and Profile can disagree
// about the withdrawal rate, one of them is lying to the user about their retirement date."
// The two surfaces do render different chrome (Plan's pl- dialog, Settings' rd- dialog — the
// design draws them that way), so what is shared is everything that could ever DISAGREE:
// which fields exist, how they are seeded from the profile, the suggested-SWR formula, the
// derived FIRE number, and the exact PUT payload.
import { authFetch } from '../../../api/client'
import { PROFILE_KEY } from '../../../hooks/serverState'
import { invalidateQueryCache } from '../../../state/queryCache'
import * as PD from './planData'

export interface FireDraft {
  monthlyRetirementSpending: number | ''
  withdrawalRate: number | ''
  expectedReturn: number | ''
  volatility: number | ''
  targetAge: number | ''
  lifeExpectancy: number | ''
  monthlySaving: number | ''
}

export const EMPTY_FIRE_DRAFT: FireDraft = {
  monthlyRetirementSpending: '',
  withdrawalRate: '',
  expectedReturn: '',
  volatility: '',
  targetAge: '',
  lifeExpectancy: '',
  monthlySaving: '',
}

// Nullable as well as optional: the profile stores an unset numeric field as `null`, and a
// caller passing its own profile shape must not have to launder that first.
export interface FireProfile {
  monthly_expenses?: number | null
  monthly_net_income?: number | null
  /** What they put aside each month — its own fact; null = never stated, so derive it. */
  monthly_saving?: number | null
  fire_monthly_retirement_spending?: number | null
  fire_withdrawal_rate_pct?: number | null
  fire_expected_return_pct?: number | null
  fire_volatility_pct?: number | null
  retirement_target_age?: number | null
  fire_life_expectancy?: number | null
}

/** What the plan assumes for the two settings people leave unset. The form shows them as the
 *  values in use rather than as an empty box with a grey hint (persona QA 2026-10-14: "it says
 *  nothing, yet my plan uses 15% and 90"). Mirrors `retirement_plan.DEFAULT_VOLATILITY_PCT` and
 *  `fire.LIFE_EXPECTANCY`; tests/test_fire_settings_defaults.py keeps the three in step. */
export const FIRE_VOLATILITY_IN_USE = 15
export const FIRE_LIFE_EXPECTANCY_IN_USE = 90

/** Seed the draft from the user's real (or derivable) data. Anything unset stays blank so the
 *  field shows its placeholder — demo-persona numbers are never pre-filled as if the user had
 *  configured them — except volatility and life expectancy, which show the value the plan is
 *  using. A stated saving wins; only without one is it what income leaves after spending. */
export function seedFireDraft(p: FireProfile): FireDraft {
  const expenses = Number(p.monthly_expenses) || 0
  const income = Number(p.monthly_net_income) || 0
  const derivedSaving = Math.max(0, Math.round(income - expenses))
  return {
    monthlyRetirementSpending: p.fire_monthly_retirement_spending ?? (expenses > 0 ? expenses : ''),
    withdrawalRate: p.fire_withdrawal_rate_pct ?? '',
    expectedReturn: p.fire_expected_return_pct ?? '',
    volatility: p.fire_volatility_pct ?? FIRE_VOLATILITY_IN_USE,
    targetAge: p.retirement_target_age ?? '',
    lifeExpectancy: p.fire_life_expectancy ?? FIRE_LIFE_EXPECTANCY_IN_USE,
    monthlySaving: p.monthly_saving ?? (derivedSaving > 0 ? derivedSaving : ''),
  }
}

/** Retirement horizon in years, or null when it isn't derivable yet. */
export function fireHorizon(s: FireDraft): number | null {
  return s.targetAge !== '' &&
    s.lifeExpectancy !== '' &&
    Number(s.lifeExpectancy) > Number(s.targetAge)
    ? Number(s.lifeExpectancy) - Number(s.targetAge)
    : null
}

/** Suggested safe withdrawal rate for this draft's horizon — the ONE shared formula
 *  (planData.suggestSWR), so this field and the Outlook caveat can never disagree. */
export function fireSwrSuggestion(s: FireDraft): number | null {
  const horizon = fireHorizon(s)
  return horizon !== null ? PD.suggestSWR(horizon) : null
}

/** What to tell someone whose withdrawal rate differs from the horizon's suggestion: which side
 *  of it they are on, and why that is a choice, not an oversight (persona QA round 7: "suggests
 *  4.25% while the plan stays at 4%" with no word on why). Null with no suggestion or a blank rate. */
export function fireSwrNote(s: FireDraft): string | null {
  const suggest = fireSwrSuggestion(s)
  const horizon = fireHorizon(s)
  if (suggest === null || horizon === null || fireSwrMatches(s)) return null
  if (s.withdrawalRate === '')
    return `${suggest}% is the usual rate for a ${horizon}-yr retirement.`
  const rate = Number(s.withdrawalRate)
  return rate < suggest
    ? `A ${horizon}-yr retirement usually allows ${suggest}%. Your plan keeps ${rate}%, the more cautious choice.`
    : `A ${horizon}-yr retirement is safer at ${suggest}%. Your plan uses ${rate}%, which is more aggressive.`
}

export function fireSwrMatches(s: FireDraft): boolean {
  const suggest = fireSwrSuggestion(s)
  return (
    suggest !== null &&
    s.withdrawalRate !== '' &&
    Math.abs(Number(s.withdrawalRate) - suggest) < 0.001
  )
}

/** Annualized spend ÷ withdrawal rate — shown live in the editor as a calculation. */
export function fireNumberOf(s: FireDraft): number | null {
  return s.monthlyRetirementSpending !== '' &&
    s.withdrawalRate !== '' &&
    Number(s.withdrawalRate) > 0
    ? PD.fireNumberFromMonthlySpend(Number(s.monthlyRetirementSpending), Number(s.withdrawalRate))
    : null
}

/** Persist the draft. Sparse by design: a blank field is "not set", not "set to zero".
 *  `monthlySaving` is stored as its own fact (`monthly_saving`) — it never rewrites income.
 *  With `known` (the profile the draft was seeded from) a saving the user left as seeded is NOT
 *  sent: the seed may be only the derived income − expenses, and sending it would pin that
 *  derived figure as a stated one (and drop a deliberate saved-plan saving for it).
 *  Resolves whether the server accepted it. */
export async function saveFireDraft(s: FireDraft, known?: FireProfile): Promise<boolean> {
  const body: Record<string, number> = {}
  if (s.monthlyRetirementSpending !== '')
    body.fire_monthly_retirement_spending = Number(s.monthlyRetirementSpending)
  if (s.withdrawalRate !== '') body.fire_withdrawal_rate_pct = Number(s.withdrawalRate)
  if (s.expectedReturn !== '') body.fire_expected_return_pct = Number(s.expectedReturn)
  // A value in use that the user never set is not saved as theirs unless they change it.
  const keptDefault = (stored: number | null | undefined, shown: number | '', inUse: number) =>
    known !== undefined && stored == null && shown === inUse
  if (
    s.volatility !== '' &&
    !keptDefault(known?.fire_volatility_pct, s.volatility, FIRE_VOLATILITY_IN_USE)
  )
    body.fire_volatility_pct = Number(s.volatility)
  if (s.targetAge !== '') body.retirement_target_age = Number(s.targetAge)
  if (
    s.lifeExpectancy !== '' &&
    !keptDefault(known?.fire_life_expectancy, s.lifeExpectancy, FIRE_LIFE_EXPECTANCY_IN_USE)
  )
    body.fire_life_expectancy = Number(s.lifeExpectancy)
  const seededSaving = known ? seedFireDraft(known).monthlySaving : undefined
  if (s.monthlySaving !== '' && s.monthlySaving !== seededSaving)
    body.monthly_saving = Number(s.monthlySaving)

  try {
    const res = await authFetch(PROFILE_KEY, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    invalidateQueryCache(PROFILE_KEY)
    invalidateQueryCache('/api/fire?')
    return res.ok
  } catch {
    return false
  }
}
