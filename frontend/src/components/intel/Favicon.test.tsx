// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { Favicon } from './Favicon'

afterEach(cleanup)

describe('Favicon', () => {
  it('renders an img from the publisher domain', () => {
    const { container } = render(
      <Favicon
        url="https://news.google.com/rss/articles/CB"
        domain="benzinga.com"
        className="asd-news-favicon"
      />,
    )
    const img = container.querySelector('img.asd-news-favicon') as HTMLImageElement
    expect(img).toBeTruthy()
    // Routes through our own auth-gated proxy (ADR 0025), not a direct publisher/third-party hit.
    expect(img.getAttribute('src')).toBe('/api/favicon?domain=benzinga.com')
    expect(container.querySelector('.favicon-fallback')).toBeNull()
  })

  it('falls back to a same-size blue box when the favicon fails to load', () => {
    const { container } = render(
      <Favicon url="https://x.com/a" domain="x.com" className="asd-news-favicon" />,
    )
    fireEvent.error(container.querySelector('img.asd-news-favicon')!)
    expect(container.querySelector('span.asd-news-favicon.favicon-fallback')).toBeTruthy()
    expect(container.querySelector('img')).toBeNull()
  })

  it('shows the blue box immediately when no favicon can be resolved', () => {
    const { container } = render(<Favicon url="" className="asd-news-favicon" />)
    expect(container.querySelector('span.asd-news-favicon.favicon-fallback')).toBeTruthy()
    expect(container.querySelector('img')).toBeNull()
  })
})
