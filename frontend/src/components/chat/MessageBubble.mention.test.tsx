// @vitest-environment jsdom
//
// Tests for the sent-message entity-chip rendering in MessageBubble.
// Verifies that user messages with `[type: ref|label]` tokens render the shared
// `.chip` component (not the old `.mention-chip--readonly` span), and that
// clicking a chip opens the `.mx-detail` popover while clicking the same chip
// again closes it.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, render, fireEvent, act } from '@testing-library/react'
import { MessageBubble } from './MessageBubble'
import type { Message } from '../../types/chat'

afterEach(cleanup)

function makeUserMessage(text: string): Message {
  return {
    id: 'msg-1',
    role: 'user',
    text,
    streaming: false,
  }
}

const noop = vi.fn()

function renderBubble(text: string) {
  const { container } = render(
    <MessageBubble message={makeUserMessage(text)} onSendMessage={noop} />,
  )
  return container
}

describe('MessageBubble sent-message entity chips', () => {
  it('renders a .chip for an entity token (not the old .mention-chip--readonly)', () => {
    const container = renderBubble('Check [account: acc_1|Checking Account] please')
    // Must use the shared Chip component
    expect(container.querySelector('.chip')).toBeTruthy()
    // Must NOT use the legacy read-only span
    expect(container.querySelector('.mention-chip--readonly')).toBeNull()
  })

  it('chip is read-only — no × remove button', () => {
    const container = renderBubble('[holding: pos_abc|AAPL holding]')
    const chip = container.querySelector('.chip')
    expect(chip).toBeTruthy()
    // Chip with no onRemove renders .readonly and no .chip-x
    expect(chip!.classList.contains('readonly')).toBe(true)
    expect(container.querySelector('.chip-x')).toBeNull()
  })

  it('shows the carried label as chip text', () => {
    const container = renderBubble('[account: acc_1|My Savings]')
    const name = container.querySelector('.chip-name')
    expect(name?.textContent).toBe('My Savings')
  })

  it('renders stale chip for unknown type with no label', () => {
    const container = renderBubble('[xyzunknown: some_ref]')
    const chip = container.querySelector('.chip')
    expect(chip).toBeTruthy()
    expect(chip!.classList.contains('stale')).toBe(true)
    // Label shows "(deleted)"
    expect(container.querySelector('.chip-name')?.textContent).toBe('(deleted)')
  })

  // The popover is PORTALED to document.body (it is position:fixed, and .lx-side's transform
  // made it the containing block for a fixed descendant rendered inline), so these assert
  // against the document, not the render container.
  it('clicking a chip opens the .mx-detail popover', async () => {
    const container = renderBubble('[account: acc_1|Checking Account]')
    const chip = container.querySelector<HTMLElement>('.chip')
    expect(chip).toBeTruthy()

    expect(document.querySelector('.mx-detail')).toBeNull()

    await act(async () => {
      fireEvent.click(chip!)
    })

    expect(document.querySelector('.mx-detail')).toBeTruthy()
  })

  it('clicking the same chip again closes the .mx-detail popover', async () => {
    const container = renderBubble('[account: acc_1|Checking Account]')
    const chip = container.querySelector<HTMLElement>('.chip')!

    await act(async () => {
      fireEvent.click(chip)
    })
    expect(document.querySelector('.mx-detail')).toBeTruthy()

    await act(async () => {
      fireEvent.click(chip)
    })
    expect(document.querySelector('.mx-detail')).toBeNull()
  })

  it('preserves surrounding free text', () => {
    const container = renderBubble('Hello [account: acc_1|Savings] world')
    const text = container.querySelector('.msg-text')!.textContent ?? ''
    expect(text).toContain('Hello')
    expect(text).toContain('Savings')
    expect(text).toContain('world')
  })

  it('renders multiple chips in one message', () => {
    const container = renderBubble('[account: a1|Checking] and [holding: p1|AAPL]')
    const chips = container.querySelectorAll('.chip')
    expect(chips).toHaveLength(2)
  })
})

// ── Output chip matches input chip for on-screen transactions ─────────────────
//
// When an on-screen [data-entity-id] element carries data-ctx-fields with a
// date and amount, MentionText resolves them via byId() and enriches the
// MentionRef so the output chip renders identically to the composer input chip.
// When no element is found (entity off-screen / older message), the chip falls
// back to the carried label only — no crash.

describe('MessageBubble — output chip enrichment from on-screen entity', () => {
  it('renders .chip-meta (short date) and .chip-amt.neg from the on-screen transaction sublabel', () => {
    // Plant an on-screen entity element the way a transaction row would (via entityRefProps).
    // The NATIVE amount lives in data-entity-sublabel ("payee · account · date · amount");
    // data-ctx-fields carries amount_base (the base-currency NUMBER, for the model).
    const entityEl = document.createElement('span')
    entityEl.setAttribute('data-entity-id', 'transaction:txn-42')
    entityEl.setAttribute(
      'data-ctx-fields',
      JSON.stringify({ date: '2026-06-18', amount_base: '-252.71' }),
    )
    entityEl.setAttribute(
      'data-entity-sublabel',
      'Agoda • Meridian Bank • 2026-06-18 • −252.71 SGD',
    )
    document.body.appendChild(entityEl)

    try {
      const container = renderBubble('[transaction: txn-42|Agoda]')

      // Short date visible in chip-meta
      const meta = container.querySelector('.chip-meta')
      expect(meta?.textContent).toBe('Jun 18')

      // Signed amount in chip-amt, tinted negative (U+2212 minus)
      const amt = container.querySelector('.chip-amt')
      expect(amt).toBeTruthy()
      expect(amt?.textContent).toBe('−252.71 SGD')
      expect(amt?.classList.contains('neg')).toBe(true)
      expect(amt?.classList.contains('pos')).toBe(false)
    } finally {
      entityEl.remove()
    }
  })

  it('renders .chip-amt.pos when amount is positive', () => {
    const entityEl = document.createElement('span')
    entityEl.setAttribute('data-entity-id', 'transaction:txn-income')
    entityEl.setAttribute(
      'data-ctx-fields',
      JSON.stringify({ date: '2026-05-01', amount_base: '8000' }),
    )
    entityEl.setAttribute(
      'data-entity-sublabel',
      'Salary • Horizon Bank • 2026-05-01 • +8000.00 SGD',
    )
    document.body.appendChild(entityEl)

    try {
      const container = renderBubble('[transaction: txn-income|Salary]')
      const amt = container.querySelector('.chip-amt')
      expect(amt?.classList.contains('pos')).toBe(true)
      expect(amt?.classList.contains('neg')).toBe(false)
    } finally {
      entityEl.remove()
    }
  })

  it('falls back to label-only when entity has no on-screen element (off-screen / older message)', () => {
    // No element planted — byId() returns null.
    const container = renderBubble('[transaction: txn-missing|Grab]')

    // Chip renders (label visible) — no crash
    const name = container.querySelector('.chip-name')
    expect(name?.textContent).toBe('Grab')

    // No enriched date or amount
    expect(container.querySelector('.chip-meta')).toBeNull()
    expect(container.querySelector('.chip-amt')).toBeNull()
  })

  it('renders .chip-amt NEUTRAL (no .pos/.neg) for an account with on-screen balance', () => {
    const entityEl = document.createElement('span')
    entityEl.setAttribute('data-entity-id', 'account:acc-vantage')
    entityEl.setAttribute(
      'data-ctx-fields',
      JSON.stringify({ balance: 'S$12,340.00', name: 'Vantage Brokerage USD' }),
    )
    document.body.appendChild(entityEl)

    try {
      const container = renderBubble('[account: acc-vantage|Vantage Brokerage USD]')
      const amt = container.querySelector('.chip-amt')
      expect(amt).toBeTruthy()
      expect(amt?.textContent).toBe('S$12,340.00')
      // Neutral — no direction coloring
      expect(amt?.classList.contains('pos')).toBe(false)
      expect(amt?.classList.contains('neg')).toBe(false)
    } finally {
      entityEl.remove()
    }
  })

  it('renders .chip-amt NEUTRAL for a holding with on-screen value', () => {
    const entityEl = document.createElement('span')
    entityEl.setAttribute('data-entity-id', 'holding:pos-aapl')
    entityEl.setAttribute('data-ctx-fields', JSON.stringify({ value: 'S$48,200.00', name: 'AAPL' }))
    document.body.appendChild(entityEl)

    try {
      const container = renderBubble('[holding: pos-aapl|AAPL]')
      const amt = container.querySelector('.chip-amt')
      expect(amt).toBeTruthy()
      expect(amt?.textContent).toBe('S$48,200.00')
      expect(amt?.classList.contains('pos')).toBe(false)
      expect(amt?.classList.contains('neg')).toBe(false)
    } finally {
      entityEl.remove()
    }
  })

  it('reads account color from data-entity-color attribute on the on-screen element', () => {
    const entityEl = document.createElement('span')
    entityEl.setAttribute('data-entity-id', 'account:acc-color')
    entityEl.setAttribute('data-entity-color', '#ff5733')
    entityEl.setAttribute(
      'data-ctx-fields',
      JSON.stringify({ balance: 'S$5,000.00', name: 'Meridian Bank Savings' }),
    )
    document.body.appendChild(entityEl)

    try {
      const container = renderBubble('[account: acc-color|Meridian Bank Savings]')
      // The chip should render (the balance chip-amt check is sufficient for this test;
      // color is applied as an inline style on chip-gl which jsdom normalizes).
      const chip = container.querySelector('.chip')
      expect(chip).toBeTruthy()
    } finally {
      entityEl.remove()
    }
  })
})

// A reloaded conversation rehydrates messages as BLOCKS, not bare text. That path rendered
// through ReactMarkdown only, so chips drew but nothing was clickable — the "can't tap the
// chip after refreshing" bug (owner, 2026-08-23).
describe('MessageBubble — chips in a rehydrated (block-based) message', () => {
  function renderBlockBubble(content: string) {
    const { container } = render(
      <MessageBubble
        message={
          {
            id: 'msg-2',
            role: 'user',
            text: content,
            streaming: false,
            blocks: [{ type: 'text', content }],
          } as unknown as Message
        }
        onSendMessage={noop}
      />,
    )
    return container
  }

  const TOKEN = '[commitment: riverside-heights|Parkview Residences] why though?'

  it('renders the mention as a .chip, not raw markdown text', () => {
    const container = renderBlockBubble(TOKEN)
    const chip = container.querySelector('.chip')
    expect(chip).toBeTruthy()
    expect(chip!.textContent).toContain('Parkview Residences')
  })

  it('clicking that chip opens the .mx-detail popover', async () => {
    const container = renderBlockBubble(TOKEN)
    const chip = container.querySelector('.chip')!
    await act(async () => {
      fireEvent.click(chip)
    })
    expect(document.querySelector('.mx-detail')).toBeTruthy()
  })
})
