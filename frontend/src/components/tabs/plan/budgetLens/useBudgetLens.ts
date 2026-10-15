// budgetLens/useBudgetLens.ts — the budgeting-method state, lifted OUT of BudgetLensPane so the
// method menu can render in Plan.tsx's sticky masthead (on the PageTabs row) while the pane it
// picks stays down in `.pl-tabwrap`.
//
// There is no previewing any more (owner, 2026-09-25 — the Budget Onboarding (cards) design):
// the Budget tab always shows the book's ONE method, the home engine. Changing it is a switch
// (confirmed in SwitchMethodModal) or, for a new book, a first-run start; comparing is a
// read-only modal.
import { useEffect, useState } from 'react'
import { rememberBudgetMethod } from '../../../../utils/budgetMethodMemory'
import { dismissBudgetLensIntro, saveMonthlyNetIncome, setHomeEngine } from './api'
import { configure } from './proportional/propApi'
import { useBudgetEngines } from './useBudgetEngines'
import type { BudgetEngineDescriptor, ProportionalTargets } from './types'

export interface BudgetLensState {
  engines: BudgetEngineDescriptor[]
  homeId: string
  loading: boolean
  /** A method switch staged for confirmation (SwitchMethodModal, mounted once in
   *  BudgetLensPane) — switching decides what the Overview widgets, the daily briefing and
   *  Lucius report, so nothing promotes a method without asking first. */
  switchTo: string | null
  requestSwitch: (id: string) => void
  /** The user confirmed — actually POST /api/planning/home-engine. */
  confirmSwitch: () => void
  cancelSwitch: () => void
  /** A NEW book picking its first method: nothing to lose, so no confirmation — it becomes
   *  home and the first-run chooser is dismissed for good. */
  startWith: (id: string) => void
  switching: boolean
  refresh: () => void
  /** True once GET /api/planning/engines has answered at least once — the chooser must not
   *  flash before this book's real "have they budgeted before" state is known. */
  introLoaded: boolean
  /** Has this book already dismissed (or completed) the first-run chooser? */
  introDismissed: boolean
  /** Does this book already have real budget data — home is zero_based (only ever the
   *  default when `budget_assignments` has a row, per
   *  services.budget_engines.base._unset_home_engine_id), OR any other engine is configured.
   *  An existing budgeter never sees the chooser over the budget they already have. */
  hasBudgetData: boolean
  compareOpen: boolean
  openCompare: () => void
  closeCompare: () => void
  /** 50/30/20's onboarding (income → split) is open. A 50/30/20 that was never set up is
   *  onboarded BEFORE it becomes the book's method — closing the modal changes nothing. */
  onboarding: boolean
  onboardError: string | null
  openOnboarding: () => void
  cancelOnboarding: () => void
  finishOnboarding: (
    income: number,
    targets: ProportionalTargets,
    excluded?: string[],
  ) => Promise<void>
}

export function useBudgetLens(): BudgetLensState {
  const engineQuery = useBudgetEngines()
  const engines = engineQuery.data?.engines ?? []
  // Fallback only for the brief pre-load window — matches the backend's own stated default
  // (services/budget_engines/base.py:home_engine_id).
  const homeId = engineQuery.data?.home_engine_id ?? 'zero_based'
  const loadedHomeId = engineQuery.data?.home_engine_id
  useEffect(() => {
    if (loadedHomeId) rememberBudgetMethod(loadedHomeId)
  }, [loadedHomeId])

  const [switching, setSwitching] = useState(false)
  const [switchTo, setSwitchTo] = useState<string | null>(null)
  const [compareOpen, setCompareOpen] = useState(false)
  // Set when onboarding opens: did it come from the first-run chooser (which it then dismisses)?
  const [onboarding, setOnboarding] = useState<{ fromFirstRun: boolean } | null>(null)
  const [onboardError, setOnboardError] = useState<string | null>(null)
  const proportionalConfigured = !!engines.find((e) => e.id === 'proportional')?.configured

  async function promote(id: string, alsoDismissIntro: boolean) {
    setSwitching(true)
    try {
      await setHomeEngine(id)
      if (alsoDismissIntro) await dismissBudgetLensIntro()
      engineQuery.refresh()
    } finally {
      setSwitching(false)
    }
  }

  async function confirmSwitch() {
    const target = switchTo
    if (!target) return
    // Cleared BEFORE the await, so a second click on Switch cannot re-fire the request.
    setSwitchTo(null)
    if (target === 'proportional' && !proportionalConfigured) {
      openOnboarding(false)
      return
    }
    await promote(target, false)
  }

  function openOnboarding(fromFirstRun: boolean) {
    setOnboardError(null)
    setOnboarding({ fromFirstRun })
  }

  // "Start budgeting": the income goes to the Profile (Outlook and FIRE read it too), then
  // `configure` saves each group's share — the groups are the book's Zero-Based groups, so there
  // is nothing to sort — and only then does 50/30/20 become the book's method (the backend
  // refuses the switch before `configure` has run).
  async function finishOnboarding(
    income: number,
    targets: ProportionalTargets,
    excluded: string[] = [],
  ) {
    const from = onboarding
    if (!from) return
    setSwitching(true)
    setOnboardError(null)
    try {
      if (income > 0) await saveMonthlyNetIncome(income)
      await configure(targets, excluded)
      await setHomeEngine('proportional')
      if (from.fromFirstRun) await dismissBudgetLensIntro()
      setOnboarding(null)
      engineQuery.refresh()
    } catch (e) {
      setOnboardError(e instanceof Error ? e.message : 'Could not set up 50/30/20 — try again.')
    } finally {
      setSwitching(false)
    }
  }

  const introLoaded = engineQuery.data !== undefined
  const introDismissed = engineQuery.data?.intro_dismissed ?? false
  const hasBudgetData =
    homeId === 'zero_based' || engines.some((e) => e.id !== 'zero_based' && e.configured)

  return {
    engines,
    homeId,
    loading: engineQuery.initialLoading && engines.length === 0,
    switchTo,
    requestSwitch: (id) => setSwitchTo(id),
    confirmSwitch,
    cancelSwitch: () => setSwitchTo(null),
    startWith: (id) => {
      if (id === 'proportional' && !proportionalConfigured) openOnboarding(true)
      else void promote(id, true)
    },
    switching,
    refresh: () => engineQuery.refresh(),
    introLoaded,
    introDismissed,
    hasBudgetData,
    compareOpen,
    openCompare: () => setCompareOpen(true),
    closeCompare: () => setCompareOpen(false),
    onboarding: onboarding !== null,
    onboardError,
    openOnboarding: () => openOnboarding(false),
    cancelOnboarding: () => setOnboarding(null),
    finishOnboarding,
  }
}
