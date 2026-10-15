// @vitest-environment jsdom
//
// The composer is the always-on, DOM-owned chat input (it replaced the textarea↔composer gate
// that lost "@" detection after the first pick). These exercise its imperative handle — the API
// ChatPanel drives: setText (prefill), insertChip (inline @ pick / external ✦), serialize (send),
// clear, isEmpty. Live-caret "@" detection rides the Selection API and is covered by the pure
// scanner tests (mentionTrigger) + serialize/readSegments; jsdom's Selection is too thin to drive.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { createRef } from 'react'
import { cleanup, render, fireEvent } from '@testing-library/react'
import { MentionComposer } from './MentionComposer'
import type { MentionComposerHandle, ChipClickPayload } from './MentionComposer'
import type { MentionRef } from '../../types/mentions'

afterEach(cleanup)

const aapl: MentionRef = { type: 'holding', ref: 'pos_123', label: 'AAPL holding' }

function renderComposer(onChipClick?: (p: ChipClickPayload) => void) {
  const ref = createRef<MentionComposerHandle>()
  const onContentChange = vi.fn()
  const onTrigger = vi.fn()
  const onSubmit = vi.fn()
  const { container } = render(
    <MentionComposer
      ref={ref}
      onSubmit={onSubmit}
      onTrigger={onTrigger}
      onContentChange={onContentChange}
      onChipClick={onChipClick}
    />,
  )
  return { ref, onContentChange, onTrigger, onSubmit, container }
}

describe('MentionComposer imperative handle', () => {
  it('starts empty (placeholder visible via .is-empty)', () => {
    const { ref, container } = renderComposer()
    expect(ref.current!.isEmpty()).toBe(true)
    expect(ref.current!.serialize()).toEqual({ text: '', mentions: [] })
    expect(container.querySelector('.chat-composer')?.classList.contains('is-empty')).toBe(true)
  })

  it('setText fills plain text, drops .is-empty, reports not-empty', () => {
    const { ref, onContentChange, container } = renderComposer()
    ref.current!.setText('hello world')
    expect(ref.current!.isEmpty()).toBe(false)
    expect(ref.current!.serialize()).toEqual({ text: 'hello world', mentions: [] })
    expect(onContentChange).toHaveBeenLastCalledWith(false)
    expect(container.querySelector('.chat-composer')?.classList.contains('is-empty')).toBe(false)
  })

  it('insertChip embeds an inline chip atom that serialize carries in DOM order', () => {
    const { ref, container } = renderComposer()
    ref.current!.setText('look at ')
    ref.current!.insertChip(aapl)
    expect(container.querySelector('.mention-chip')).toBeTruthy()
    const { text, mentions } = ref.current!.serialize()
    expect(mentions).toEqual([aapl])
    expect(text.startsWith('look at')).toBe(true)
    expect(text).toContain('[holding: pos_123|AAPL holding]')
  })

  it('a chips-only composer still serializes the mention (empty text body is sendable)', () => {
    const { ref } = renderComposer()
    ref.current!.insertChip(aapl)
    const { text, mentions } = ref.current!.serialize()
    expect(mentions).toEqual([aapl])
    expect(text).toBe('[holding: pos_123|AAPL holding]')
    expect(ref.current!.isEmpty()).toBe(false)
  })

  it('clear empties everything and restores .is-empty', () => {
    const { ref, onContentChange, container } = renderComposer()
    ref.current!.setText('something')
    ref.current!.clear()
    expect(ref.current!.isEmpty()).toBe(true)
    expect(ref.current!.serialize()).toEqual({ text: '', mentions: [] })
    expect(onContentChange).toHaveBeenLastCalledWith(true)
    expect(container.querySelector('.chat-composer')?.classList.contains('is-empty')).toBe(true)
  })

  // Bug 2: transaction chip with date + signed amount renders .chip-meta and .chip-amt
  it('transaction chip with chipDate + chipAmt renders .chip-meta and .chip-amt.neg', () => {
    const { ref, container } = renderComposer()
    const txn: MentionRef = {
      type: 'transaction',
      ref: 'txn_456',
      label: 'Cold Storage',
      chipDate: '2024-06-15',
      chipAmt: '-184.20 SGD',
      chipAmtNeg: true,
    }
    ref.current!.insertChip(txn)
    const chip = container.querySelector('.mention-chip')
    expect(chip).not.toBeNull()
    const metaEl = chip!.querySelector('.chip-meta')
    expect(metaEl).not.toBeNull()
    expect(metaEl!.textContent).toBe('2024-06-15')
    const amtEl = chip!.querySelector('.chip-amt')
    expect(amtEl).not.toBeNull()
    expect(amtEl!.textContent).toBe('-184.20 SGD')
    expect(amtEl!.classList.contains('neg')).toBe(true)
    expect(amtEl!.classList.contains('pos')).toBe(false)
  })

  it('transaction chip with pos chipAmt renders .chip-amt.pos', () => {
    const { ref, container } = renderComposer()
    const txn: MentionRef = {
      type: 'transaction',
      ref: 'txn_789',
      label: 'Monthly salary',
      chipDate: '2024-06-17',
      chipAmt: '+14200 SGD',
      chipAmtNeg: false,
    }
    ref.current!.insertChip(txn)
    const amtEl = container.querySelector('.chip-amt')
    expect(amtEl).not.toBeNull()
    expect(amtEl!.classList.contains('pos')).toBe(true)
    expect(amtEl!.classList.contains('neg')).toBe(false)
  })

  it('non-transaction chip does not render .chip-meta or .chip-amt', () => {
    const { ref, container } = renderComposer()
    const acct: MentionRef = { type: 'account', ref: 'acct_1', label: 'Meridian Bank Multiplier' }
    ref.current!.insertChip(acct)
    expect(container.querySelector('.chip-meta')).toBeNull()
    expect(container.querySelector('.chip-amt')).toBeNull()
  })

  // Bug 4: insertChip on an empty editor produces no leading newline/whitespace before the chip
  it('insertChip on empty editor inserts chip at start with no leading newline', () => {
    const { ref, container } = renderComposer()
    ref.current!.insertChip(aapl)
    const editor = container.querySelector('.chat-composer')!
    const firstChild = editor.childNodes[0]
    // First child must be the chip element, not a text node
    expect(firstChild?.nodeType).toBe(Node.ELEMENT_NODE)
    expect((firstChild as HTMLElement).classList.contains('mention-chip')).toBe(true)
  })

  // Bug #54 (recurring): real browsers leave a <br> placeholder in an empty contenteditable;
  // a trigger-less insert (drag-drop) must clear it so the chip isn't pushed below a blank line.
  it('insertChip clears the empty-state <br> so there is no leading blank line', () => {
    const { ref, container } = renderComposer()
    const editor = container.querySelector('.chat-composer') as HTMLElement
    editor.innerHTML = '<br>' // simulate the browser's empty-contenteditable placeholder
    ref.current!.insertChip(aapl)
    expect(editor.querySelector('br')).toBeNull()
    expect((editor.childNodes[0] as HTMLElement)?.classList?.contains('mention-chip')).toBe(true)
  })

  // Chip-body click opens the detail popover via onChipClick.
  it('clicking a chip body fires onChipClick with type, ref, label, and chipEl', () => {
    const onChipClick = vi.fn()
    const { ref, container } = renderComposer(onChipClick)
    ref.current!.insertChip(aapl)
    const chip = container.querySelector('.mention-chip') as HTMLElement
    expect(chip).not.toBeNull()
    // Click the chip name span (child of chip, not the remove button)
    const nameEl = chip.querySelector('.chip-name') as HTMLElement
    fireEvent.click(nameEl)
    expect(onChipClick).toHaveBeenCalledTimes(1)
    const payload = onChipClick.mock.calls[0]![0] as ChipClickPayload
    expect(payload.type).toBe('holding')
    expect(payload.ref).toBe('pos_123')
    expect(payload.label).toBe('AAPL holding')
    expect(payload.chipEl).toBe(chip)
  })

  it('composer chips have no × remove button (keyboard-delete only)', () => {
    const { ref, container } = renderComposer()
    ref.current!.insertChip(aapl)
    // Part 4: no .chip-x in composer chips — user removes via Backspace
    expect(container.querySelector('.chip-x')).toBeNull()
    expect(container.querySelector('[data-chip-remove]')).toBeNull()
    // The chip atom is still present
    expect(container.querySelector('.mention-chip')).not.toBeNull()
  })

  // Bug #54 — drop onto the contenteditable must not leave a stray newline/br.
  // The MentionComposer now has onDrop={e => e.preventDefault()} so the browser
  // cannot inject text/plain (the drag's dataTransfer payload) into the editor.
  it('a drop event on the contenteditable is prevented (no stray text node)', () => {
    const { ref, container } = renderComposer()
    const editor = container.querySelector('.chat-composer') as HTMLElement
    expect(editor).not.toBeNull()

    // Simulate a drop with text/plain payload — mimics the browser inserting
    // the dragged entity ref string into the contenteditable.
    const dropEvent = new Event('drop', { bubbles: true, cancelable: true })
    Object.defineProperty(dropEvent, 'dataTransfer', {
      value: {
        getData: (type: string) => (type === 'text/plain' ? 'some-entity-ref' : ''),
        files: { length: 0 },
      },
      writable: false,
    })
    const preventDefaultSpy = vi.spyOn(dropEvent, 'preventDefault')
    editor.dispatchEvent(dropEvent)

    // preventDefault must have been called, blocking native text insertion.
    expect(preventDefaultSpy).toHaveBeenCalled()
    // The editor must remain empty (no stray text injected).
    expect(ref.current!.isEmpty()).toBe(true)
    expect(editor.querySelectorAll('br').length).toBe(0)
  })
})
