// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'
import { AssetDetailSkeleton } from './AssetDetailSkeleton'

afterEach(cleanup)

describe('AssetDetailSkeleton', () => {
  it('renders the seeded name + code as real header text', () => {
    render(
      <AssetDetailSkeleton name="Space Exploration Technologies Corp." ticker="SPCX • NASDAQ" />,
    )
    expect(screen.getByRole('heading', { name: /Space Exploration/ })).toBeTruthy()
    expect(screen.getByText('SPCX • NASDAQ')).toBeTruthy()
  })

  it('falls back to placeholder bars when nothing is known', () => {
    render(<AssetDetailSkeleton />)
    expect(screen.queryByRole('heading')).toBeNull()
  })

  it('renders the real sub-tab control when provided (static nav, no shimmer)', () => {
    render(<AssetDetailSkeleton subTabs={<button type="button">Overview</button>} />)
    expect(screen.getByRole('button', { name: 'Overview' })).toBeTruthy()
  })
})
