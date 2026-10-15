import type { BaseOverride } from './usePlanState'

export async function persistFirePlan(
  baseline: BaseOverride,
  saveBaseline: (value: BaseOverride) => Promise<void>,
  syncProfile: () => Promise<void>,
  baselineAlreadySaved: boolean,
): Promise<boolean> {
  if (!baselineAlreadySaved) await saveBaseline(baseline)
  try {
    await syncProfile()
    return true
  } catch {
    return false
  }
}
