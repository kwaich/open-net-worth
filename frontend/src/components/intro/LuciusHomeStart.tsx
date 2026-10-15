// The empty Lucius home: the first time, a tour of his controls; with nothing in the book yet,
// four ways to start under the composer too.
import { useAccounts } from '../../hooks/serverState/useAccounts'
import { LuciusHomeTour } from './LuciusTours'
import { requestGuide } from '../../state/guideRequests'
import type { GuideRequest } from '../../state/guideRequests'
import './intro.css'

const STARTS: { label: string; message: string; guide?: GuideRequest }[] = [
  {
    label: 'Set up my accounts',
    message:
      'Help me set up my accounts: ask which banks, cards, brokerages and loans I have, add ' +
      'them, then help me bring in their transactions. Ask me one thing at a time.',
  },
  {
    label: 'Add what I own',
    message:
      'Help me add the things I own, like a home, a car, a watch or gold, with what each is ' +
      'worth. Ask me one thing at a time.',
  },
  {
    label: 'Log from my bank emails',
    message:
      'Keep my book up to date from my bank, card and broker emails: walk me through ' +
      'connecting my email, then set up the automation that logs them.',
  },
  {
    label: 'Send a screenshot or statement',
    message:
      'I want to add my accounts from a screenshot or statement. Tell me what to send, then ' +
      'read it and set them up.',
    guide: 'attach',
  },
]

export function LuciusHomeStart({ onSend }: { onSend: (message: string) => void }) {
  const { data } = useAccounts()
  if (data === undefined) return null
  if (data.length > 0) return <LuciusHomeTour />
  // An empty book gets the ways to start AND the controls tour: the paperclip it shows is the
  // most useful control before anything is in the book. One guide at a time still holds.
  return (
    <>
      <LuciusHomeTour />
      <div className="fi-starts" aria-label="Ways to start">
        {STARTS.map((s) => (
          <button
            key={s.label}
            type="button"
            className="fi-start"
            onClick={() => {
              onSend(s.message)
              // Light up the paperclip now: the next thing to do is attach the file.
              if (s.guide) requestGuide(s.guide)
            }}
          >
            {s.label}
          </button>
        ))}
      </div>
    </>
  )
}
