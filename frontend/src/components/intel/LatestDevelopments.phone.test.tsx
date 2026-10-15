// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { LatestDevelopments } from './LatestDevelopments'
import type { NewsItem } from '../../types/assetDetail'

vi.mock('../../hooks/useIsMobile', () => ({ useIsMobile: () => true }))

afterEach(cleanup)

function items(n: number): NewsItem[] {
  return Array.from({ length: n }, (_, i) => ({
    title: `Item ${i + 1}`,
    source: 'Src',
    url: `https://e.com/${i + 1}`,
    published_at: '2026-06-19',
  })) as NewsItem[]
}

describe('LatestDevelopments on a phone', () => {
  it('caps at the phone limit when one is given — 5 rows, not the desktop 10 (owner ask 2026-09-22)', () => {
    render(
      <LatestDevelopments
        items={items(20)}
        offset={6}
        limit={10}
        phoneLimit={5}
        onToggle={() => {}}
      />,
    )
    expect(screen.getByText('Item 11')).toBeTruthy() // 5th development row
    expect(screen.queryByText('Item 12')).toBeNull() // 6th: behind View all
    expect(screen.getByText('View all (14)')).toBeTruthy() // the count is still everything
  })

  it('falls back to the desktop limit without a phone limit', () => {
    render(<LatestDevelopments items={items(20)} offset={6} limit={10} onToggle={() => {}} />)
    expect(screen.getByText('Item 16')).toBeTruthy()
    expect(screen.queryByText('Item 17')).toBeNull()
  })
})
