// @vitest-environment jsdom
//
// Intro modals and tour bubbles draw EXAMPLE figures, and must never reach Lucius as the user's
// data. The real components are rendered here, so renaming one of their root classes fails this
// test instead of silently putting a sample "Cash on hand S$4,124" in front of the model.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { FeatureIntro } from '../components/intro/FeatureIntro'
import { PageTour } from '../components/intro/PageTour'
import { resetGuidedForTests } from '../components/intro/tourTarget'
import { harvestDOM } from './harvest'
import {
  getScreenSnapshot,
  screenSuggestions,
  setScreenFacts,
  setScreenPrompts,
} from './screenContract'

beforeEach(() => resetGuidedForTests())
afterEach(() => {
  cleanup()
  setScreenFacts(null)
  setScreenPrompts(null)
})

function Page() {
  return (
    <div data-ctx-screen="Plan">
      <h1 data-ctx-title="Cash flow">Cash flow</h1>
      <div data-ctx-region="content">
        <div data-tour="balance" data-ctx-fact="Your balance">
          S$2,150
        </div>
        <div data-ctx data-entity-id="account:real" data-ctx-type="account">
          Real account
        </div>
      </div>
    </div>
  )
}

const SAMPLE_STEP = {
  title: 'See where your cash goes',
  points: [{ title: 'Sample', body: 'A month with S$4,124 left over' }],
  hero: (
    <div data-ctx-fact="Sample balance" data-entity-id="account:example" data-ctx>
      S$4,124 example balance
    </div>
  ),
}

function snapshotText(): string {
  const snap = getScreenSnapshot(new Set(['account:real']))
  return JSON.stringify([snap.facts, snap.raw, snap.title])
}

describe('guidance never becomes a screen fact', () => {
  it('ignores an intro modal, keeping the real screen behind it', async () => {
    render(
      <>
        <Page />
        <FeatureIntro
          open
          steps={[SAMPLE_STEP]}
          lucius={{ label: 'Set it up', onClick: vi.fn() }}
          secondary={{ label: 'Set up myself', onClick: vi.fn() }}
          onClose={vi.fn()}
        />
      </>,
    )
    expect(await screen.findByRole('dialog')).toBeTruthy()
    const text = snapshotText()
    expect(text).not.toContain('4,124')
    expect(text).not.toContain('Open dialog')
    expect(text).not.toContain('DIALOG IS OPEN')
    expect(text).toContain('S$2,150')
    expect(text).toContain('Cash flow')
    expect(harvestDOM().map((r) => r.id)).not.toContain('account:example')
  })

  it('ignores a tour bubble the same way', async () => {
    render(
      <>
        <Page />
        <PageTour
          steps={[{ target: 'balance', title: 'Your balance', body: 'Shows S$9,999 as a sample.' }]}
          onClose={vi.fn()}
        />
      </>,
    )
    expect(await screen.findByRole('dialog', { name: 'Your balance' })).toBeTruthy()
    const text = snapshotText()
    expect(text).not.toContain('9,999')
    expect(text).not.toContain('Open dialog')
    expect(text).toContain('S$2,150')
  })

  it('does not take the guide for the screen when suggesting prompts', async () => {
    setScreenPrompts(({ modal }) => (modal ? [`about ${modal}`] : ['about the screen']))
    render(
      <>
        <Page />
        <PageTour
          steps={[{ target: 'balance', title: 'Your balance', body: 'Sample.' }]}
          onClose={vi.fn()}
        />
      </>,
    )
    await screen.findByRole('dialog', { name: 'Your balance' })
    expect(screenSuggestions().join(' ')).not.toContain('Your balance')
  })
})
