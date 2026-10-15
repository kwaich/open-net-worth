// @vitest-environment jsdom
//
// Owner report (mobile Lucius chat): Backspace right after a mention chip did NOT delete the
// chip — the caret just stepped left past it. The chip-eating logic lived only in the keydown
// handler and only matched `e.key === 'Backspace'`: a touch keyboard routes deletion through
// the IME path (`key: 'Unidentified'`, keyCode 229) so keydown never matched, and WebKit often
// parks a tapped caret INSIDE the non-editable chip, which the two narrow caret cases missed.
// The browser's native delete then treated the contenteditable=false atom as a boundary and
// moved the caret over it instead of removing it. Deletion now also rides `beforeinput`
// (inputType is what every keyboard reports) and resolves the caret from any container.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { createRef } from 'react'
import { cleanup, render, fireEvent } from '@testing-library/react'
import { MentionComposer } from './MentionComposer'
import type { MentionComposerHandle } from './MentionComposer'
import type { MentionRef } from '../../types/mentions'

afterEach(cleanup)

const aapl: MentionRef = { type: 'holding', ref: 'pos_123', label: 'AAPL holding' }

function renderComposer() {
  const ref = createRef<MentionComposerHandle>()
  const onContentChange = vi.fn()
  const { container } = render(
    <MentionComposer
      ref={ref}
      onSubmit={vi.fn()}
      onTrigger={vi.fn()}
      onContentChange={onContentChange}
    />,
  )
  const editor = container.querySelector<HTMLElement>('.chat-composer')!
  return { ref, editor, onContentChange }
}

function placeCaret(node: Node, offset: number) {
  const range = document.createRange()
  range.setStart(node, offset)
  range.collapse(true)
  const sel = window.getSelection()!
  sel.removeAllRanges()
  sel.addRange(range)
}

function softKeyboardBackspace(editor: HTMLElement): boolean {
  // A touch keyboard: keydown carries no usable key, then beforeinput carries the intent.
  fireEvent.keyDown(editor, { key: 'Unidentified', keyCode: 229 })
  const ev = new InputEvent('beforeinput', {
    inputType: 'deleteContentBackward',
    bubbles: true,
    cancelable: true,
  })
  return editor.dispatchEvent(ev)
}

describe('MentionComposer — deleting a chip', () => {
  it('Backspace right where insertChip leaves the caret (after the separator space) removes the chip in ONE press', () => {
    // Owner report, second round: "I still have to tap delete twice." insertChip appends a
    // single space after the chip as the caret's home; the first Backspace ate that invisible
    // space and only the second reached the chip. The separator belongs to the chip.
    const { ref, editor, onContentChange } = renderComposer()
    ref.current!.setText('see ')
    ref.current!.insertChip(aapl)

    fireEvent.keyDown(editor, { key: 'Backspace' })

    expect(editor.querySelector('.mention-chip')).toBeNull()
    expect(ref.current!.serialize()).toEqual({ text: 'see', mentions: [] })
    expect(onContentChange).toHaveBeenLastCalledWith(false)
  })

  it('soft-keyboard Backspace with the caret at the end of the separator text node removes the chip', () => {
    const { ref, editor } = renderComposer()
    ref.current!.insertChip(aapl)
    const trailing = editor.querySelector('.mention-chip')!.nextSibling!
    placeCaret(trailing, 1)

    const notCancelled = softKeyboardBackspace(editor)

    expect(notCancelled).toBe(false)
    expect(editor.querySelector('.mention-chip')).toBeNull()
    expect(editor.textContent).toBe('')
  })

  it('Backspace after "[chip]  " (an extra typed space) deletes only that space natively', () => {
    const { ref, editor } = renderComposer()
    ref.current!.insertChip(aapl)
    const trailing = editor.querySelector('.mention-chip')!.nextSibling!
    trailing.textContent = '  '
    placeCaret(trailing, 2)

    expect(softKeyboardBackspace(editor)).toBe(true)
    expect(editor.querySelector('.mention-chip')).not.toBeNull()
  })

  it('soft-keyboard Backspace (unidentified keydown + beforeinput) right after a chip removes it', () => {
    const { ref, editor, onContentChange } = renderComposer()
    ref.current!.insertChip(aapl)
    const trailing = editor.querySelector('.mention-chip')!.nextSibling!
    placeCaret(trailing, 0)

    const notCancelled = softKeyboardBackspace(editor)

    expect(notCancelled).toBe(false)
    expect(editor.querySelector('.mention-chip')).toBeNull()
    expect(ref.current!.serialize().mentions).toEqual([])
    expect(onContentChange).toHaveBeenLastCalledWith(true)
  })

  it('Backspace with the caret parked INSIDE the chip (WebKit tap placement) removes it', () => {
    const { ref, editor } = renderComposer()
    ref.current!.insertChip(aapl)
    const nameText = editor.querySelector('.chip-name')!.firstChild!
    placeCaret(nameText, nameText.textContent!.length)

    fireEvent.keyDown(editor, { key: 'Backspace' })

    expect(editor.querySelector('.mention-chip')).toBeNull()
    expect(ref.current!.serialize().mentions).toEqual([])
  })

  it('forward Delete with the caret right before a chip removes it', () => {
    const { ref, editor } = renderComposer()
    ref.current!.setText('see ')
    ref.current!.insertChip(aapl)
    const lead = editor.firstChild!
    placeCaret(lead, lead.textContent!.length)

    fireEvent.keyDown(editor, { key: 'Delete' })

    expect(editor.querySelector('.mention-chip')).toBeNull()
    expect(ref.current!.serialize()).toEqual({ text: 'see', mentions: [] })
  })

  it('Backspace at the end of text that PRECEDES a chip leaves the chip alone', () => {
    const { editor, ref } = renderComposer()
    ref.current!.setText('see ')
    ref.current!.insertChip(aapl)
    const lead = editor.firstChild!
    placeCaret(lead, lead.textContent!.length)

    const notCancelled = softKeyboardBackspace(editor)

    expect(notCancelled).toBe(true)
    expect(editor.querySelector('.mention-chip')).not.toBeNull()
  })

  it('Backspace inside plain text leaves the chip alone (native deletion proceeds)', () => {
    const { editor, ref } = renderComposer()
    ref.current!.insertChip(aapl)
    const trailing = editor.querySelector('.mention-chip')!.nextSibling!
    trailing.textContent = ' hi'
    placeCaret(trailing, 3)

    const notCancelled = softKeyboardBackspace(editor)

    expect(notCancelled).toBe(true)
    expect(editor.querySelector('.mention-chip')).not.toBeNull()
  })
})
