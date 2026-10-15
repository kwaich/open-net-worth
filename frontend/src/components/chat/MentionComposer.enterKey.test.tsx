// @vitest-environment jsdom
//
// Live report 2026-08-11, two bugs found together:
//
// 1. Mobile: a touch keyboard's Return key never carries a real Shift modifier — there's no
//    physical shift key to hold — so "Enter sends, Shift+Enter breaks the line" has no way to
//    reach the newline case on mobile. Enter must break the line there instead; sending is the
//    visible Send button's job. Desktop keeps Enter-sends/Shift+Enter-newline unchanged.
//
// 2. Desktop: "shift+enter does nothing, the second press works" — every press was structurally
//    correct (confirmed live via instrumentation: identical code path, identical values, every
//    time). Root cause was insertText('\n') — a bare newline CHARACTER at a text-node boundary
//    has ambiguous caret geometry in every browser: the blinking caret stays rendered at the END
//    of the just-broken line until something else forces the layout engine to commit to the new,
//    still-empty line — which only happens once a SECOND break disambiguates it. Fixed by
//    inserting a real <br> element instead (unambiguous caret positioning everywhere, and already
//    the shape the serializer expects — readSegments' own comment: "a browser injected <br> on
//    Enter").
//
// serialize().text is NOT used to assert the break landed: it intentionally .trim()s trailing
// whitespace, so a break at the very end of the draft disappears there by design. Asserting on
// the DOM directly (a real <br> element) is the reliable, direct signal.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { createRef } from 'react'
import { cleanup, render, fireEvent } from '@testing-library/react'
import { MentionComposer } from './MentionComposer'
import type { MentionComposerHandle } from './MentionComposer'

afterEach(cleanup)

/** Forces useIsMobile() true (jsdom's default matchMedia stub reports desktop — see
 *  vitest.setup.ts). `globalThis.matchMedia` is installed as a getter-only accessor by
 *  vitest.setup.ts, so a plain `window.matchMedia = ...` assignment silently no-ops — must
 *  redefine the property instead. */
function mockMobile(isMobile: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = ((query: string) => ({
    matches: isMobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

function renderComposer() {
  const ref = createRef<MentionComposerHandle>()
  const onSubmit = vi.fn()
  const { container } = render(
    <MentionComposer ref={ref} onSubmit={onSubmit} onTrigger={vi.fn()} onContentChange={vi.fn()} />,
  )
  const editor = container.querySelector('.chat-composer')!
  return { ref, onSubmit, editor }
}

describe('MentionComposer Enter key — desktop', () => {
  it('Enter sends, does not insert a break', () => {
    const restore = mockMobile(false)
    try {
      const { ref, onSubmit, editor } = renderComposer()
      ref.current!.setText('hello')
      fireEvent.keyDown(editor, { key: 'Enter' })
      expect(onSubmit).toHaveBeenCalledTimes(1)
      expect(editor.querySelector('br')).toBeNull()
    } finally {
      restore()
    }
  })

  it('Shift+Enter inserts a real <br> (unambiguous caret positioning), does not send', () => {
    const restore = mockMobile(false)
    try {
      const { ref, onSubmit, editor } = renderComposer()
      ref.current!.setText('hello')
      fireEvent.keyDown(editor, { key: 'Enter', shiftKey: true })
      expect(onSubmit).not.toHaveBeenCalled()
      expect(editor.innerHTML).toBe('hello<br>')
    } finally {
      restore()
    }
  })

  it('a break followed by more text round-trips through serialize as a real newline', () => {
    const restore = mockMobile(false)
    try {
      const { ref, editor } = renderComposer()
      ref.current!.setText('hello')
      fireEvent.keyDown(editor, { key: 'Enter', shiftKey: true })
      ref.current!.insertChip.length // no-op reference to keep ref used before further DOM edits
      editor.appendChild(document.createTextNode('world'))
      expect(ref.current!.serialize().text).toBe('hello\nworld')
    } finally {
      restore()
    }
  })
})

describe('MentionComposer Enter key — mobile (no physical Shift key on a touch keyboard)', () => {
  it('Enter breaks the line instead of sending', () => {
    const restore = mockMobile(true)
    try {
      const { ref, onSubmit, editor } = renderComposer()
      ref.current!.setText('hello')
      fireEvent.keyDown(editor, { key: 'Enter' })
      expect(onSubmit).not.toHaveBeenCalled()
      expect(editor.innerHTML).toBe('hello<br>')
    } finally {
      restore()
    }
  })

  it('Shift+Enter (unreachable on a touch keyboard, but harmless if it ever fires) still breaks the line, never sends', () => {
    const restore = mockMobile(true)
    try {
      const { ref, onSubmit, editor } = renderComposer()
      ref.current!.setText('hello')
      fireEvent.keyDown(editor, { key: 'Enter', shiftKey: true })
      expect(onSubmit).not.toHaveBeenCalled()
      expect(editor.innerHTML).toBe('hello<br>')
    } finally {
      restore()
    }
  })
})
