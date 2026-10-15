// @vitest-environment jsdom

import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { CitationChip } from './CitationChip'
import type { Citation } from '../../types/assetDetail'

afterEach(cleanup)

const VALID: Citation = {
  url: 'https://example.com/article',
  title: 'Example Article',
  domain: 'example.com',
  published_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(), // 2h ago
  quote: 'This is a quoted excerpt from the article.',
}

describe('CitationChip — valid url', () => {
  it('renders as a link with the title', () => {
    render(<CitationChip source={VALID} />)
    const link = screen.getByRole('link') as HTMLAnchorElement
    expect(link.getAttribute('href')).toBe(VALID.url)
    expect(link.getAttribute('target')).toBe('_blank')
    expect(link.getAttribute('rel')).toContain('noopener')
    expect(link.getAttribute('rel')).toContain('noreferrer')
  })

  it('shows the title text', () => {
    render(<CitationChip source={VALID} />)
    expect(screen.getByText('Example Article')).toBeTruthy()
  })

  it('shows a relative date', () => {
    render(<CitationChip source={VALID} />)
    // 2h ago should render as "2h"
    expect(screen.getByText('2h')).toBeTruthy()
  })

  it('popover contains the quote', () => {
    const { container } = render(<CitationChip source={VALID} />)
    const popover = container.querySelector('.citation-chip__popover')
    expect(popover).toBeTruthy()
    expect(popover!.textContent).toContain(VALID.quote)
  })
})

describe('CitationChip — invalid/empty url', () => {
  it('renders a non-link span (no href) for an empty url', () => {
    const source: Citation = { url: '' }
    render(<CitationChip source={source} />)
    // Must not render a link element
    expect(screen.queryByRole('link')).toBeNull()
    // Must render the no-link class
    const { container } = render(<CitationChip source={source} />)
    expect(container.querySelector('.citation-chip--no-link')).toBeTruthy()
  })

  it('renders a non-link span for a non-URL string', () => {
    const source: Citation = { url: 'not a url at all', title: 'Bad source' }
    const { container } = render(<CitationChip source={source} />)
    expect(screen.queryByRole('link')).toBeNull()
    expect(container.querySelector('.citation-chip--no-link')).toBeTruthy()
  })

  it('falls back to domain as title when no title is provided', () => {
    const source: Citation = { url: 'https://reuters.com/news/123', domain: 'reuters.com' }
    render(<CitationChip source={source} />)
    expect(screen.getByText('reuters.com')).toBeTruthy()
  })

  it('truncates a long title to ~18 chars with ellipsis', () => {
    const source: Citation = { url: 'https://x.com', title: 'A very long title that exceeds limit' }
    render(<CitationChip source={source} />)
    const titleEl = document.querySelector('.citation-chip__title')
    expect(titleEl!.textContent!.length).toBeLessThanOrEqual(18)
    expect(titleEl!.textContent).toContain('…')
  })
})
