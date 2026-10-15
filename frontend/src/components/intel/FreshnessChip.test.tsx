// @vitest-environment jsdom

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { FreshnessChip } from './FreshnessChip'

afterEach(cleanup)

function dot(container: HTMLElement): HTMLElement {
  return container.querySelector('.freshness-chip__dot') as HTMLElement
}
function label(container: HTMLElement): HTMLElement {
  return container.querySelector('.freshness-chip__label') as HTMLElement
}
function chip(container: HTMLElement): HTMLElement {
  return container.querySelector('.freshness-chip') as HTMLElement
}

// ── Backend grade prop (preferred path) ────────────────────────────────────

describe('FreshnessChip — grade prop (backend-computed)', () => {
  it('grade="fresh" → --fresh modifier class (green dot)', () => {
    const { container } = render(<FreshnessChip grade="fresh" />)
    expect(chip(container).className).toContain('freshness-chip--fresh')
  })

  it('grade="aging" → --stale modifier class (amber dot)', () => {
    const { container } = render(<FreshnessChip grade="aging" />)
    expect(chip(container).className).toContain('freshness-chip--stale')
  })

  it('grade="stale" → --old modifier class (red dot)', () => {
    const { container } = render(<FreshnessChip grade="stale" />)
    expect(chip(container).className).toContain('freshness-chip--old')
  })

  it('renders dot and label elements', () => {
    const { container } = render(<FreshnessChip grade="fresh" />)
    expect(dot(container)).toBeTruthy()
    expect(label(container)).toBeTruthy()
  })

  it('includes label prefix in displayed text when provided', () => {
    const { container } = render(<FreshnessChip grade="fresh" label="Prices" />)
    expect(label(container).textContent).toContain('Prices')
  })
})

// ── Timestamp fallback path (no grade) ─────────────────────────────────────

describe('FreshnessChip — timestamp fallback', () => {
  it('timestamp < 24h ago → --fresh', () => {
    const ts = new Date(Date.now() - 3 * 3600 * 1000).toISOString() // 3h ago
    const { container } = render(<FreshnessChip timestamp={ts} />)
    expect(chip(container).className).toContain('freshness-chip--fresh')
  })

  it('timestamp 2d ago → --stale (amber)', () => {
    const ts = new Date(Date.now() - 2 * 86400 * 1000).toISOString()
    const { container } = render(<FreshnessChip timestamp={ts} />)
    expect(chip(container).className).toContain('freshness-chip--stale')
  })

  it('timestamp 10d ago → --old (red)', () => {
    const ts = new Date(Date.now() - 10 * 86400 * 1000).toISOString()
    const { container } = render(<FreshnessChip timestamp={ts} />)
    expect(chip(container).className).toContain('freshness-chip--old')
  })

  it('overdue check: age > intervalSeconds*1.5 → --old even if recent', () => {
    // 10 minutes old, but interval is 5 minutes → 10 > 5*1.5=7.5 → old
    const ts = new Date(Date.now() - 10 * 60 * 1000).toISOString()
    const { container } = render(<FreshnessChip timestamp={ts} intervalSeconds={300} />)
    expect(chip(container).className).toContain('freshness-chip--old')
  })

  it('sets title attr to ISO string for accessibility', () => {
    const ts = new Date(Date.now() - 1 * 3600 * 1000).toISOString()
    const { container } = render(<FreshnessChip timestamp={ts} />)
    const el = chip(container)
    expect(el.getAttribute('title')).toBeTruthy()
  })
})

// ── No timestamp ────────────────────────────────────────────────────────────

describe('FreshnessChip — no timestamp', () => {
  it('no timestamp and no grade → --old class', () => {
    const { container } = render(<FreshnessChip />)
    expect(chip(container).className).toContain('freshness-chip--old')
  })

  it('label text says "not checked yet" when no timestamp provided', () => {
    const { container } = render(<FreshnessChip />)
    expect(label(container).textContent).toContain('not checked yet')
  })
})
