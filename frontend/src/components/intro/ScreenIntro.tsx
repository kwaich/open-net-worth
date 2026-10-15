// Mounted once by the shell: shows the current screen's first-visit intro while that screen
// is still empty, once per user (remembered on the server). Each screen's "empty" reads data
// the app already caches, so an intro that will never show costs no extra request.
import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useRouterState } from '@tanstack/react-router'
import { useAccounts, useProfile } from '../../hooks/serverState'
import { useCommitments } from '../../hooks/useCommitments'
import { useSkills } from '../../hooks/useSkills'
import { listAutomations } from '../../api/automations'
import { useQueryCache } from '../../state/queryCache'
import { useConnectors } from '../tabs/connectors/useConnectors'
import { useBudgetEngines } from '../tabs/plan/budgetLens/useBudgetEngines'
import { usePortfolioContext } from '../../state/portfolio'
import { useLuciusChatOptional } from '../../state/luciusChat'
import { FeatureIntro } from './FeatureIntro'
import { PageTour } from './PageTour'
import { findTarget } from './tourTarget'
import { HIDDEN_INTROS, SCREEN_INTROS } from './screenIntros'
import type { IntroScreen, ScreenIntroConfig } from './screenIntros'
import { useScreenFill } from './screenState'
import { useSeenIntro } from './useSeenIntro'

/** How a screen meets the user on a first visit: still empty (set it up), already holding
 *  data (Lucius may have filled it during onboarding: explain it), or not yet (null). */
type Mode = 'empty' | 'filled'
type Gate = (props: { children: (mode: Mode | null) => ReactNode }) => ReactNode
const modeOf = (count: number | undefined): Mode | null =>
  count === undefined ? null : count === 0 ? 'empty' : 'filled'

// Null until the data has loaded: an intro never shows on a guess.
const Accounts: Gate = ({ children }) => children(modeOf(useAccounts().data?.length))
const Positions: Gate = ({ children }) => {
  const { positionRows, hasLoaded } = usePortfolioContext()
  return children(hasLoaded ? modeOf(positionRows.length) : null)
}
// Empty is the same test Plan uses to show its budget chooser: no method set up, not dismissed.
const Budget: Gate = ({ children }) => {
  const { data } = useBudgetEngines()
  if (!data) return children(null)
  const hasBudget =
    (data.home_engine_id ?? 'zero_based') === 'zero_based' ||
    data.engines.some((e) => e.id !== 'zero_based' && e.configured)
  return children(!data.intro_dismissed && !hasBudget ? 'empty' : 'filled')
}
// Cash Flow knows whether it has anything to project (income, spending or a dated event) and
// reports it; until it has, the intro waits.
const CashFlow: Gate = ({ children }) => children(useScreenFill('plan-cashflow'))
// Outlook introduces itself once it can draw one. Until then its own setup dialog asks for the
// birth date and spending it needs, and an intro on top would be a second popup.
const Outlook: Gate = ({ children }) => {
  const { data: accounts } = useAccounts()
  const { data: profile } = useProfile()
  const p = profile as Record<string, unknown> | undefined
  const spending = Number(p?.fire_monthly_retirement_spending ?? p?.monthly_expenses ?? 0)
  const born = typeof p?.date_of_birth === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(p.date_of_birth)
  // Answering Outlook's setup questions already introduced it (they mark it seen there).
  const seen = (p?.seen_intros as string[] | undefined)?.includes('plan-outlook')
  return children(!seen && accounts?.length && born && spending > 0 ? 'empty' : null)
}
const Commitments: Gate = ({ children }) => {
  const { commitments, loading } = useCommitments()
  return children(loading ? null : modeOf(commitments.length))
}

const Automations: Gate = ({ children }) => {
  const { data } = useQueryCache('intro:automations', listAutomations)
  return children(modeOf(data?.total))
}
// The app installs a skill of its own (statement-import) that reads as user-made, so "has
// skills" can't tell a new user from a seasoned one: the intro always offers making one.
const Skills: Gate = ({ children }) => {
  const { loading } = useSkills()
  return children(loading ? null : 'empty')
}
// Set up means a connector the user switched on. The price feeds and the default web search
// come on by themselves and don't count.
const DEFAULT_ON = new Set(['web_search:duckduckgo'])
const Connectors: Gate = ({ children }) => {
  const { connectors, initialLoading } = useConnectors()
  if (initialLoading) return children(null)
  return children(
    modeOf(connectors.filter((c) => c.enabled && c.pausable && !DEFAULT_ON.has(c.id)).length),
  )
}

// Plan writes its remembered tab into the URL before it applies a link to another one, so the
// sub-tab flickers on load. An intro mounted and torn down in that flicker looped until Plan
// crashed; the Plan intros wait until the tab has held still.
function useSettled<T>(value: T, ms = 350): T | undefined {
  const [settled, setSettled] = useState<{ value: T } | null>(null)
  useEffect(() => {
    const timer = setTimeout(() => setSettled({ value }), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled && settled.value === value ? settled.value : undefined
}

const GATES: Record<IntroScreen, Gate> = {
  accounts: Accounts,
  plan: Budget,
  'plan-cashflow': CashFlow,
  'plan-outlook': Outlook,
  // The Dashboard fills from accounts.
  dashboard: Accounts,
  investments: Positions,
  intel: Positions,
  commitments: Commitments,
  automations: Automations,
  skills: Skills,
  connectors: Connectors,
}

export function ScreenIntro({
  screen,
  luciusBeside = false,
  onAskLucius,
}: {
  screen: string
  /** Lucius is open beside the screen (never on a phone, where he is his own tab). */
  luciusBeside?: boolean
  onAskLucius: (text: string) => void
}) {
  // Wait while he is mid-reply beside you rather than cover what he is saying; an idle panel
  // beside the screen is no reason to skip the intro (after first run it is nearly always open).
  const replying = !!useLuciusChatOptional()?.streaming
  const sub = useSettled(
    useRouterState({ select: (s) => (s.location.search as { sub?: string }).sub }),
  )
  const suppressed = luciusBeside && replying
  // Plan is three tabs, each with its own intro, shown when that tab is the one open.
  const name =
    screen === 'plan'
      ? sub === 'cashflow'
        ? 'plan-cashflow'
        : sub === 'outlook'
          ? 'plan-outlook'
          : sub === 'budget'
            ? 'plan'
            : null
      : screen
  if (!name || !(name in SCREEN_INTROS)) return null
  const key = name as IntroScreen
  if (HIDDEN_INTROS.has(key)) return null
  const Gate = GATES[key]
  return (
    <Gate key={key}>
      {(mode) => (
        <Intro
          screen={key}
          config={SCREEN_INTROS[key]}
          mode={suppressed ? null : mode}
          onAskLucius={onAskLucius}
        />
      )}
    </Gate>
  )
}

function Intro({
  screen,
  config,
  mode,
  onAskLucius,
}: {
  screen: IntroScreen
  config: ScreenIntroConfig
  mode: Mode | null
  onAskLucius: (text: string) => void
}) {
  const { unseen, markSeen } = useSeenIntro(screen)
  const [dismissed, setDismissed] = useState(false)
  const [pointing, setPointing] = useState(false)
  // The mode the intro opened in holds while it is up: adding the first account from Lucius
  // beside it must not swap its buttons under the user.
  const [shownMode, setShownMode] = useState<Mode | null>(null)
  if (mode && !shownMode && !dismissed && unseen) setShownMode(mode)
  const open = !dismissed && unseen && !!shownMode
  const filled = shownMode === 'filled'

  const close = () => {
    setDismissed(true)
    markSeen()
  }
  const start = filled ? config.filled?.start : config.start
  // "Set up myself" shows where to start, when the screen has that control on show.
  const startHere = () => {
    close()
    if (start && findTarget(start.target, 'page')) setPointing(true)
  }
  if (pointing && start) return <PageTour steps={[start]} onClose={() => setPointing(false)} />
  return (
    <FeatureIntro
      open={open}
      steps={config.steps}
      lucius={{
        label: filled ? 'Walk me through it' : (config.luciusLabel ?? 'Set up with Lucius'),
        onClick: () => {
          close()
          onAskLucius(
            filled
              ? `Walk me through my ${config.name} screen: what am I looking at, and what is ` +
                  'worth doing next? Keep it short.'
              : config.lucius,
          )
        },
      }}
      secondary={{
        label: filled
          ? (config.filled?.label ?? 'Got it')
          : (config.secondaryLabel ?? (start ? 'Set up myself' : 'Got it')),
        onClick: startHere,
      }}
      onClose={close}
    />
  )
}
