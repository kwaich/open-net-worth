// The Assets page's first-visit intro and its tour. Shown once per person, on the first visit.
import { useState } from 'react'
import type { ReactNode } from 'react'
import {
  Banknote,
  Car,
  ChartColumn,
  Coins,
  House,
  Palette,
  Plus,
  ShieldCheck,
  Watch,
} from 'lucide-react'
import { FeatureIntro } from '../../intro/FeatureIntro'
import type { IntroStep } from '../../intro/FeatureIntro'
import { OrbitHero } from '../../intro/OrbitHero'
import type { OrbitItem } from '../../intro/OrbitHero'
import { PageTour } from '../../intro/PageTour'
import type { TourStep } from '../../intro/PageTour'
import { useSeenIntro } from '../../intro/useSeenIntro'
import { sendNextPrefill } from '../../../state/luciusSendIntent'

// Every kind of thing the page holds, orbiting your net worth; the dashed last icon is a
// category of your own. Labels ride along as tooltips and for screen readers.
const KINDS: OrbitItem[] = [
  { label: 'Cash', Icon: Banknote, color: 'var(--cat-cash)' },
  { label: 'Stocks', Icon: ChartColumn, color: 'var(--cat-invest)' },
  { label: 'Home', Icon: House, color: 'var(--cat-property)' },
  { label: 'Car', Icon: Car, color: 'var(--cat-vehicle)' },
  { label: 'Watch', Icon: Watch, color: 'var(--cat-watch)' },
  { label: 'Art', Icon: Palette, color: 'var(--cat-art)' },
  { label: 'Gold', Icon: Coins, color: 'var(--cat-metal)' },
  { label: 'Insurance', Icon: ShieldCheck, color: 'var(--cat-policy)' },
  { label: 'Your own kind', Icon: Plus },
]

const STEP: IntroStep = {
  title: 'Your whole net worth, in one place',
  hero: <OrbitHero title={['Your whole net worth,', 'in one place']} items={KINDS} />,
  heroCarriesTitle: true,
  points: [
    {
      title: 'Everything you own counts.',
      body: 'Accounts, home, car, watch, or a kind you make up.',
    },
    {
      title: 'Better advice from Lucius.',
      body: 'It sees the full picture, so its advice fits you.',
    },
  ],
}

export const ASSETS_TOUR: TourStep[] = [
  {
    target: 'assets-accounts',
    title: 'Start with your accounts',
    body: 'Bank, brokerage, retirement and cards. Their balances follow your transactions.',
  },
  {
    target: 'assets-things',
    title: 'Then what you own',
    body: 'A home, car, gold or a policy. Give it a value and update it when that changes.',
  },
  {
    target: 'assets-add',
    title: 'Something not listed',
    body: 'A business stake or a private deal? Add it here as a kind of your own.',
  },
]

export const LUCIUS_SETUP =
  'Help me set up my Assets page: my bank, investment and retirement accounts, my cards, and ' +
  'the things I own like a home, a car or a watch. If it is easier, I can send you ' +
  'screenshots, statements or photos instead of typing. Ask me one thing at a time.'

export function useAssetsIntro(
  /** Null until the page knows; 'filled' once the book holds anything (Lucius may have
   *  brought it in during onboarding), when the intro explains instead of setting up. */
  mode: 'empty' | 'filled' | null,
  onAskLucius: (text: string) => void,
): { element: ReactNode } {
  const { unseen, markSeen } = useSeenIntro('assets')
  // Opens on its own once, on the first visit.
  const [dismissed, setDismissed] = useState(false)
  const [touring, setTouring] = useState(false)
  // The mode it opened in holds while it is up, so its buttons never swap under the user.
  const [shownMode, setShownMode] = useState<'empty' | 'filled' | null>(null)
  if (mode && !shownMode && !dismissed && unseen) setShownMode(mode)
  const open = !dismissed && unseen && !!shownMode
  const filled = (shownMode ?? mode) === 'filled'

  const close = () => {
    setDismissed(true)
    markSeen()
  }

  const element = (
    <>
      <FeatureIntro
        open={open}
        steps={[STEP]}
        lucius={{
          label: filled ? 'Walk me through it' : 'Set up with Lucius',
          onClick: () => {
            close()
            sendNextPrefill()
            onAskLucius(
              filled
                ? 'Walk me through my Assets screen: what am I looking at, and what is worth ' +
                    'adding next? Keep it short.'
                : LUCIUS_SETUP,
            )
          },
        }}
        secondary={{
          label: filled ? 'Got it' : 'Set up myself',
          onClick: () => {
            close()
            if (!filled) setTouring(true)
          },
        }}
        onClose={close}
      />
      {touring && <PageTour steps={ASSETS_TOUR} onClose={() => setTouring(false)} />}
    </>
  )
  return { element }
}
