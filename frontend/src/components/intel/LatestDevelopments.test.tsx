// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { LatestDevelopments } from './LatestDevelopments'
import type { NewsItem } from '../../types/assetDetail'

afterEach(cleanup)

function items(n: number): NewsItem[] {
  return Array.from({ length: n }, (_, i) => ({
    title: `Item ${i + 1}`,
    source: 'Src',
    url: `https://e.com/${i + 1}`,
    published_at: '2026-06-19',
  })) as NewsItem[]
}

describe('LatestDevelopments', () => {
  it('caps at `limit` rows by default with a "View all (N)" toggle', () => {
    // 4 already shown as cards (offset) + 12 developments → default shows 6 of them
    render(<LatestDevelopments items={items(16)} offset={4} limit={6} onToggle={() => {}} />)
    expect(screen.getByText('Item 10')).toBeTruthy() // offset4 → 6th dev row = Item 10 (shown)
    expect(screen.queryByText('Item 11')).toBeNull() // 7th dev row hidden by the cap
    expect(screen.getByText('View all (12)')).toBeTruthy()
  })

  it('shows all rows + "Show less" when expanded', () => {
    render(
      <LatestDevelopments items={items(16)} offset={4} limit={6} expanded onToggle={() => {}} />,
    )
    expect(screen.getByText('Item 16')).toBeTruthy() // last dev row now visible
    expect(screen.getByText('Show less')).toBeTruthy()
  })

  it('fires onToggle when the toggle is clicked', () => {
    const onToggle = vi.fn()
    render(<LatestDevelopments items={items(16)} offset={4} limit={6} onToggle={onToggle} />)
    fireEvent.click(screen.getByText('View all (12)'))
    expect(onToggle).toHaveBeenCalledOnce()
  })

  it('no toggle when remaining rows fit within the limit', () => {
    render(<LatestDevelopments items={items(8)} offset={4} limit={6} onToggle={() => {}} />)
    expect(screen.queryByText(/View all/)).toBeNull() // 8-4 = 4 rows ≤ 6 → no toggle
  })

  it('renders a publisher favicon per row from the domain (not the GN wrapper host)', () => {
    const item = {
      title: 'RKLB news - MarketBeat',
      source: 'MarketBeat',
      url: 'https://news.google.com/rss/articles/CBM',
      domain: 'marketbeat.com',
      published_at: '2026-06-19',
      image: null,
    } as NewsItem
    const { container } = render(
      <LatestDevelopments items={[item]} offset={0} onToggle={() => {}} />,
    )
    const favicon = container.querySelector('.asd-dev-favicon') as HTMLImageElement
    expect(favicon).toBeTruthy()
    expect(favicon.getAttribute('src')).toBe('/api/favicon?domain=marketbeat.com')
  })

  // Owner ask 2026-08-31: title only, never a subtitle/snippet — superseded the earlier
  // 2026-08-30 "show the raw source snippet" decision this test used to cover.
  it('never renders a summary paragraph, even when snippet is present', () => {
    const item = {
      title: 'RKLB wins new contract',
      source: 'MarketBeat',
      url: 'https://e.com/1',
      published_at: '2026-06-19',
      snippet: 'Rocket Lab announced a new launch agreement worth $40 million...',
    } as NewsItem
    const { container } = render(
      <LatestDevelopments items={[item]} offset={0} onToggle={() => {}} />,
    )
    expect(
      screen.queryByText('Rocket Lab announced a new launch agreement worth $40 million...'),
    ).toBeNull()
    expect(container.querySelector('.asd-dev-summary')).toBeNull()
  })

  it('renders no summary paragraph when snippet is absent', () => {
    const { container } = render(
      <LatestDevelopments items={items(1)} offset={0} onToggle={() => {}} />,
    )
    expect(container.querySelector('.asd-dev-summary')).toBeNull()
  })
})
