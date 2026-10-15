// @vitest-environment jsdom
// Connecting other AI apps and iPhone Shortcuts moved to their own Settings sections
// (MCP, iPhone Shortcuts); Developer keeps logs, code execution, SQL, extensions, scripts.
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('../../../../api/client', () => ({
  authFetch: vi.fn().mockResolvedValue(new Response('{}', { status: 500 })),
}))

import { DeveloperSection } from './DeveloperSection'

afterEach(cleanup)

it('Developer no longer hosts the MCP client configs or iPhone Shortcuts', () => {
  const { container } = render(<DeveloperSection toast={vi.fn()} />)
  const text = container.textContent ?? ''
  expect(screen.getAllByText('Application logs').length).toBeGreaterThan(0)
  expect(screen.getAllByText('Code execution').length).toBeGreaterThan(0)
  expect(screen.getAllByText('Extensions').length).toBeGreaterThan(0)
  expect(screen.getAllByText('Bundled script skills').length).toBeGreaterThan(0)
  for (const gone of [
    'Connect an external client',
    'iPhone Shortcuts',
    'Phone shortcuts',
    'Show QR code',
    'Claude Desktop',
  ])
    expect(text).not.toContain(gone)
})
