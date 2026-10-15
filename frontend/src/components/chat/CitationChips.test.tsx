// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach } from 'vitest'
import { CitationChips } from './lucius/CitationChips'
import type { Citation } from '../../types/chat'

afterEach(cleanup)

const CITE_WITH_URL: Citation = {
  n: 1,
  title: 'Apple Inc.',
  source: 'Bloomberg',
  url: 'https://bloomberg.com/apple',
}
const CITE_NO_URL: Citation = { n: 2, title: 'AAPL 10-K', source: 'SEC EDGAR' }
const CITE_TITLE_ONLY: Citation = { n: 3, title: 'Reuters article' }

describe('CitationChips', () => {
  it('renders nothing when cites is empty', () => {
    const { container } = render(<CitationChips cites={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders a .lx-cites container with one chip per citation', () => {
    const { container } = render(<CitationChips cites={[CITE_WITH_URL, CITE_NO_URL]} />)
    const row = container.querySelector('.lx-cites')
    expect(row).not.toBeNull()
    const chips = container.querySelectorAll('.lx-cite')
    expect(chips).toHaveLength(2)
  })

  it('shows the citation number in .lx-cite-n', () => {
    render(<CitationChips cites={[CITE_WITH_URL]} />)
    expect(screen.getByText('1')).toBeTruthy()
  })

  it('shows source label in .lx-cite-src, falling back to title when source is absent', () => {
    const { container } = render(<CitationChips cites={[CITE_WITH_URL, CITE_TITLE_ONLY]} />)
    const srcs = container.querySelectorAll('.lx-cite-src')
    // First cite has source = "Bloomberg"
    expect(srcs[0]?.textContent).toBe('Bloomberg')
    // Third cite has no source — falls back to title
    expect(srcs[1]?.textContent).toBe('Reuters article')
  })

  it('renders an <a> with href + target=_blank + rel=noreferrer when url is present', () => {
    const { container } = render(<CitationChips cites={[CITE_WITH_URL]} />)
    const a = container.querySelector('.lx-cite') as HTMLAnchorElement
    expect(a.tagName).toBe('A')
    expect(a.getAttribute('href')).toBe('https://bloomberg.com/apple')
    expect(a.getAttribute('target')).toBe('_blank')
    expect(a.getAttribute('rel')).toBe('noreferrer')
  })

  it('renders href="#" when url is absent (click is no-op)', () => {
    const { container } = render(<CitationChips cites={[CITE_NO_URL]} />)
    const a = container.querySelector('.lx-cite') as HTMLAnchorElement
    expect(a.getAttribute('href')).toBe('#')
  })

  it('sets title attribute to the citation title', () => {
    const { container } = render(<CitationChips cites={[CITE_WITH_URL]} />)
    const a = container.querySelector('.lx-cite') as HTMLAnchorElement
    expect(a.getAttribute('title')).toBe('Apple Inc.')
  })

  it('falls back to index+1 for n when n is not provided', () => {
    const citeNoN = { title: 'FT', source: 'Financial Times', url: 'https://ft.com' } as Citation
    // Simulate a cite missing the n field — the component should use i+1
    const { container } = render(<CitationChips cites={[citeNoN]} />)
    const n = container.querySelector('.lx-cite-n')
    // n is undefined on the object, so i+1 = 1
    expect(n?.textContent).toBe('1')
  })

  it('renders multiple cites in order with correct n values', () => {
    const cites: Citation[] = [
      { n: 1, source: 'Reuters', url: 'https://reuters.com' },
      { n: 2, source: 'CNBC', url: 'https://cnbc.com' },
      { n: 3, source: 'WSJ', url: 'https://wsj.com' },
    ]
    const { container } = render(<CitationChips cites={cites} />)
    const ns = container.querySelectorAll('.lx-cite-n')
    expect(ns[0]?.textContent).toBe('1')
    expect(ns[1]?.textContent).toBe('2')
    expect(ns[2]?.textContent).toBe('3')
    const srcs = container.querySelectorAll('.lx-cite-src')
    expect(srcs[0]?.textContent).toBe('Reuters')
    expect(srcs[1]?.textContent).toBe('CNBC')
    expect(srcs[2]?.textContent).toBe('WSJ')
  })
})
