// @vitest-environment jsdom
//
// Phone: the mention picker renders IN PLACE inside the composer and is anchored to it by CSS
// (.ac--inline) — never portaled to <body>, never positioned with viewport numbers. Owner
// reports, three rounds on iOS with the keyboard up: behind the keyboard, then one row too
// low covering the text being typed. On the landing screen the composer is pinned at the
// bottom too, so the desktop-only "open downward" mode must be ignored there.

import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Autocomplete } from './Autocomplete'
import type { UseMentionablesResult } from '../../../hooks/useMentionables'

afterEach(cleanup)
Element.prototype.scrollIntoView = vi.fn()

vi.mock('../../../hooks/useMentionables', () => ({
  useMentionables: (): UseMentionablesResult => ({
    items: [],
    loading: false,
    error: false,
    retry: vi.fn(),
    remoteItems: [],
  }),
}))
vi.mock('../../../lucius/screenContract', () => ({ allHandles: () => [] }))
vi.mock('../../../lucius/identity', () => ({ Spark: () => null, Identity: () => null }))

vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => true }))

describe('Autocomplete on the phone', () => {
  it('renders in place with .ac--inline — no body portal, no fixed positioning, never downward', () => {
    const anchor = document.createElement('div')
    document.body.appendChild(anchor)
    const ref = createRef<HTMLElement | null>()
    ref.current = anchor

    const { container } = render(
      <Autocomplete
        mode="@"
        query=""
        visibleIds={new Set()}
        onPick={vi.fn()}
        onClose={vi.fn()}
        anchorRef={ref}
        openDown
      />,
    )

    const popup = container.querySelector<HTMLElement>('.ac')
    expect(popup).not.toBeNull()
    expect(popup!.classList.contains('ac--inline')).toBe(true)
    expect(popup!.classList.contains('ac--down')).toBe(false)
    expect(popup!.style.position).toBe('')
    expect(popup!.style.top).toBe('')
    // Not portaled: the popup is inside the render container, not a direct child of <body>.
    expect(popup!.parentElement).toBe(container)
  })
})
