// @vitest-environment jsdom
//
// The screen-awareness header names the screen the user is looking at. Plan keeps its three
// sub-tabs mounted and only moves its title attribute when the user switches, so the header
// has to follow that attribute (QA 2026-10-14: "Plan — Budget" shown while on Plan › Outlook).

import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import { ScreenContextBar } from './ScreenContextBar'

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
})

vi.mock('../../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({
    enabled: true,
    setEnabled: vi.fn(),
    pinnedHandleIds: [],
    togglePin: vi.fn(),
    clearPins: vi.fn(),
    snapshot: null,
  }),
}))

vi.mock('../../../lucius/screenContract', () => ({
  getScreenSnapshot: () => {
    const title = document.querySelector('[data-ctx-title]')?.getAttribute('data-ctx-title')
    return { title: title ?? 'this screen', facts: [], raw: '', handles: [] }
  },
}))

vi.mock('../../../lucius/identity', () => ({
  hSvg: () => '',
}))

const NO_IDS = new Set<string>()

async function frames() {
  await act(async () => {
    await new Promise<void>((done) =>
      requestAnimationFrame(() => requestAnimationFrame(() => done())),
    )
  })
}

describe('ScreenContextBar title', () => {
  it('follows the screen title when only its attribute changes', async () => {
    const marker = document.createElement('span')
    marker.setAttribute('data-ctx-title', 'Plan — Budget')
    document.body.appendChild(marker)

    const { container } = render(<ScreenContextBar visibleIds={NO_IDS} />)
    await frames()
    expect(container.textContent).toContain('Plan — Budget')

    marker.setAttribute('data-ctx-title', 'Plan — Outlook')
    await frames()

    expect(container.textContent).toContain('Plan — Outlook')
    expect(container.textContent).not.toContain('Plan — Budget')
  })
})
