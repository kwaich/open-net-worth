// @vitest-environment jsdom
//
// Composer.noVision.test.tsx — the "this model can't see images" notice (ADR 2026-07-26).
//
// Kept apart from Composer.test.tsx because this is the only composer behaviour that needs
// an api/client mock (attaching a file triggers an upload) — folding that into the shared
// file would put a fetch mock under every unrelated render test there.
//
// What matters here is which of TWO messages appears, since they tell the user to do
// different things: a reader is configured (the image gets transcribed) vs none is (Lucius
// won't see the attachment at all).

import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRef } from 'react'
import { render, fireEvent, cleanup } from '@testing-library/react'
import { LuciusComposer } from './Composer'
import type { LuciusComposerHandle } from './Composer'

afterEach(cleanup)

vi.mock('../../../state/chatComposer', () => ({
  useChatComposer: () => ({
    pendingMentions: [],
    respondingAgent: null,
    pendingSkills: [],
    setPendingSkills: vi.fn(),
    clearPendingMentions: vi.fn(),
  }),
}))

vi.mock('../../../state/screenSnapshot', () => ({
  useScreenSnapshot: () => ({
    enabled: false,
    setEnabled: vi.fn(),
    pinnedHandleIds: [],
    togglePin: vi.fn(),
    clearPins: vi.fn(),
    snapshot: null,
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
  useMentionables: () => ({ items: [], loading: false }),
}))

// The attachment chip is added optimistically, before the upload resolves — so a rejected
// upload still exercises the notice, which is all this file is about.
vi.mock('../../../api/client', () => ({
  authFetch: () => Promise.reject(new Error('upload not exercised here')),
}))

// jsdom implements neither of these; the composer calls createObjectURL for the image
// thumbnail on attach and revokes it on removal.
URL.createObjectURL = () => 'blob:test'
URL.revokeObjectURL = () => {}

function renderComposer(props: { noVisionModel?: string | null; imageReader?: string } = {}) {
  const ref = createRef<LuciusComposerHandle>()
  render(
    <LuciusComposer
      ref={ref}
      onSend={vi.fn()}
      visibleIds={new Set<string>()}
      screenAwarenessDisabled
      {...props}
    />,
  )
  return ref
}

function attach(name: string, type: string) {
  const input = document.querySelector<HTMLInputElement>('input[type="file"]')!
  const file = new File(['x'], name, { type })
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  fireEvent.change(input)
}

const notice = () => document.querySelector('.lx-att-warn')

describe('no-vision notice', () => {
  it('is absent before anything is attached', () => {
    renderComposer({ noVisionModel: 'glm-4.6' })
    expect(notice()).toBeNull()
  })

  it('is absent for a model that can see images', () => {
    renderComposer({ noVisionModel: null })
    attach('receipt.jpg', 'image/jpeg')
    expect(notice()).toBeNull()
  })

  it('names the model and the reader when a reader is configured', () => {
    renderComposer({ noVisionModel: 'glm-4.6', imageReader: 'anthropic:claude-haiku-4-5' })
    attach('receipt.jpg', 'image/jpeg')
    const text = notice()?.textContent ?? ''
    expect(text).toContain('glm-4.6')
    expect(text).toContain('anthropic:claude-haiku-4-5')
    expect(text).toContain('read it')
  })

  it('says the attachment will NOT be seen when no reader is configured, and where to fix it', () => {
    renderComposer({ noVisionModel: 'glm-4.6', imageReader: '' })
    attach('receipt.jpg', 'image/jpeg')
    const text = notice()?.textContent ?? ''
    expect(text).toContain('no model is set to read them')
    expect(text).toContain('won’t see this attachment')
    expect(text).toContain('Task models')
  })

  it('stays absent for a NON-image attachment — a CSV needs no vision', () => {
    renderComposer({ noVisionModel: 'glm-4.6', imageReader: '' })
    attach('statement.csv', 'text/csv')
    expect(notice()).toBeNull()
  })
})
