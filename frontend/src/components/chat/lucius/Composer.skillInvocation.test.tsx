// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRef } from 'react'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { ChatComposerProvider } from '../../../state/chatComposer'
import { LuciusComposer } from './Composer'
import type { LuciusComposerHandle } from './Composer'

afterEach(cleanup)

vi.mock('../../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({
    enabled: false,
    pinnedHandleIds: [],
  }),
}))

vi.mock('../../../lucius/screenContract', () => ({
  getScreenSnapshot: () => ({ title: 'Test', facts: [], raw: '', handles: [] }),
  captureAndSerialize: () => null,
  allHandles: () => [],
}))

vi.mock('../../../lucius/identity', () => ({
  GLYPH_SVG: {},
  TYPE_MARKER_SVG: {},
  hSvg: () => '',
  Spark: () => null,
  Identity: () => null,
  IdentityInner: () => null,
}))

vi.mock('../../../hooks/useMentionables', () => ({
  useMentionables: () => ({
    items: [
      {
        type: 'skill',
        id: 'account-balance-tracer',
        label: 'account-balance-tracer',
        sublabel: 'Trace an account balance',
        value: 'account-balance-tracer',
        icon: '/',
        surface: 'verb',
        onScreen: false,
      },
      {
        type: 'skill',
        id: 'statement-reconciler',
        label: 'statement-reconciler',
        sublabel: 'Reconcile a statement',
        value: 'statement-reconciler',
        icon: '/',
        surface: 'verb',
        onScreen: false,
      },
    ],
    loading: false,
    error: false,
    retry: vi.fn(),
    remoteItems: [],
  }),
}))

describe('LuciusComposer explicit skill invocation', () => {
  it('does not render a persistent active mode for an invoked skill', () => {
    const legacyConversationState = {
      activeSkills: [{ skillId: 'account-balance-tracer', name: 'Account balance tracer' }],
      onDeactivateSkill: vi.fn(),
    }
    const { container, queryByText } = render(
      <ChatComposerProvider>
        <LuciusComposer
          {...legacyConversationState}
          onSend={vi.fn()}
          visibleIds={new Set<string>()}
          screenAwarenessDisabled
        />
      </ChatComposerProvider>,
    )

    expect(queryByText('Active skills')).toBeNull()
    expect(container.querySelector('.lx-active-skills')).toBeNull()
  })

  it('stages a / picker choice as the structured skill, not an ordinary mention', () => {
    const ref = createRef<LuciusComposerHandle>()
    const { container } = render(
      <ChatComposerProvider>
        <LuciusComposer
          ref={ref}
          onSend={vi.fn()}
          visibleIds={new Set<string>()}
          screenAwarenessDisabled
        />
      </ChatComposerProvider>,
    )

    const slashButton = container.querySelectorAll<HTMLButtonElement>('.lx-tool')[2]!
    fireEvent.click(slashButton)
    fireEvent.click(document.querySelector<HTMLButtonElement>('.ac-item')!)

    expect(ref.current!.serialize()).toMatchObject({
      text: '[skill: account-balance-tracer|account-balance-tracer]',
      mentions: [],
      skills: ['account-balance-tracer'],
    })
    expect(container.querySelector('.mention-chip')?.textContent).toContain(
      'account-balance-tracer',
    )
  })

  it('does not invisibly invoke a staged skill after its chip is deleted', () => {
    const ref = createRef<LuciusComposerHandle>()
    const { container } = render(
      <ChatComposerProvider>
        <LuciusComposer
          ref={ref}
          onSend={vi.fn()}
          visibleIds={new Set<string>()}
          screenAwarenessDisabled
        />
      </ChatComposerProvider>,
    )

    const slashButton = container.querySelectorAll<HTMLButtonElement>('.lx-tool')[2]!
    fireEvent.click(slashButton)
    fireEvent.click(document.querySelector<HTMLButtonElement>('.ac-item')!)
    container.querySelector('.mention-chip')!.remove()

    expect(ref.current!.serialize().skills).toEqual([])
  })

  it('preserves ordered multiple explicit skill selections', () => {
    const ref = createRef<LuciusComposerHandle>()
    const { container } = render(
      <ChatComposerProvider>
        <LuciusComposer
          ref={ref}
          onSend={vi.fn()}
          visibleIds={new Set<string>()}
          screenAwarenessDisabled
        />
      </ChatComposerProvider>,
    )

    const slashButton = container.querySelectorAll<HTMLButtonElement>('.lx-tool')[2]!
    fireEvent.click(slashButton)
    fireEvent.click(document.querySelectorAll<HTMLButtonElement>('.ac-item')[0]!)
    fireEvent.click(slashButton)
    fireEvent.click(document.querySelectorAll<HTMLButtonElement>('.ac-item')[1]!)

    expect(ref.current!.serialize()).toMatchObject({
      mentions: [],
      skills: ['account-balance-tracer', 'statement-reconciler'],
    })
  })
})
