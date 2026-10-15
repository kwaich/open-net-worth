/**
 * Add-an-MCP-server flow: config-snippet parsing + id derivation.
 *
 * The flow's contract is that pasting is NOT a separate mode — a pasted snippet lands in
 * the very same fields typing does, so the parser has to produce exactly the partial form
 * the typed path would have produced. These tests pin the three snippet shapes real READMEs
 * use, and that anything else declines rather than half-filling the form.
 */

import { describe, it, expect } from 'vitest'
import {
  guessServerName,
  isValidMcpUrl,
  parsePastedConfig,
  slugify,
  uniqueServerId,
} from './mcpConfig'

describe('parsePastedConfig — the three snippet shapes', () => {
  it('parses an `mcpServers` wrapper (the Claude Desktop / README Quick Start shape)', () => {
    const snippet = JSON.stringify({
      mcpServers: {
        'interactive-brokers': {
          command: 'npx',
          args: ['-y', '@vendor/ibkr-mcp'],
          env: { IBKR_TOKEN: 'tok-123' },
        },
      },
    })

    expect(parsePastedConfig(snippet)).toEqual({
      transport: 'local',
      command: 'npx',
      args: '-y @vendor/ibkr-mcp',
      env: 'IBKR_TOKEN=tok-123',
    })
  })

  it('parses a `servers` wrapper', () => {
    const snippet = JSON.stringify({
      servers: { docs: { url: 'https://mcp.example.com/sse' } },
    })

    expect(parsePastedConfig(snippet)).toEqual({
      transport: 'remote',
      url: 'https://mcp.example.com/sse',
      auth: 'none',
    })
  })

  it('parses a bare server map with no wrapper key', () => {
    const snippet = JSON.stringify({
      'my-server': { command: 'uvx', args: ['some-mcp-server'] },
    })

    expect(parsePastedConfig(snippet)).toEqual({
      transport: 'local',
      command: 'uvx',
      args: 'some-mcp-server',
      env: '',
    })
  })

  it('takes the FIRST entry of a multi-server snippet — bulk import is a different endpoint', () => {
    const snippet = JSON.stringify({
      mcpServers: {
        first: { command: 'npx', args: ['-y', 'first-server'] },
        second: { command: 'npx', args: ['-y', 'second-server'] },
      },
    })

    expect(parsePastedConfig(snippet)).toMatchObject({ args: '-y first-server' })
  })

  it('infers API-key auth from a remote entry carrying headers', () => {
    const snippet = JSON.stringify({
      mcpServers: {
        secured: { url: 'https://mcp.example.com', headers: { Authorization: 'Bearer x' } },
      },
    })

    expect(parsePastedConfig(snippet)).toEqual({
      transport: 'remote',
      url: 'https://mcp.example.com',
      auth: 'key',
    })
  })

  it('declines half-typed JSON instead of throwing — the box parses on every keystroke', () => {
    expect(parsePastedConfig('{ "mcpServers": {')).toBeNull()
    expect(parsePastedConfig('')).toBeNull()
    expect(parsePastedConfig('not json at all')).toBeNull()
  })

  it('declines a snippet with no url and no command', () => {
    expect(parsePastedConfig(JSON.stringify({ mcpServers: { x: { note: 'hi' } } }))).toBeNull()
    expect(parsePastedConfig(JSON.stringify({ mcpServers: {} }))).toBeNull()
    expect(parsePastedConfig(JSON.stringify([1, 2, 3]))).toBeNull()
  })

  it('coerces non-string args/env values rather than emitting "[object Object]" silently', () => {
    const snippet = JSON.stringify({
      mcpServers: { x: { command: 'npx', args: ['-y', 42], env: { PORT: 8080 } } },
    })

    expect(parsePastedConfig(snippet)).toEqual({
      transport: 'local',
      command: 'npx',
      args: '-y 42',
      env: 'PORT=8080',
    })
  })
})

describe('isValidMcpUrl — Test connect is gated on this, not just non-empty', () => {
  it('accepts a plausible http(s) URL, including local/LAN hosts with no dotted domain', () => {
    expect(isValidMcpUrl('https://mcp.example.com')).toBe(true)
    expect(isValidMcpUrl('https://mcp.stripe.com')).toBe(true)
    // A self-hosted/dev MCP server is a completely normal target — a stricter regex that
    // required a dotted domain would reject every one of these real, working URLs.
    expect(isValidMcpUrl('http://localhost:8080/mcp')).toBe(true)
    expect(isValidMcpUrl('http://127.0.0.1:8787')).toBe(true)
    expect(isValidMcpUrl('http://my-internal-server/mcp')).toBe(true)
  })

  it('rejects anything that is not a real URL', () => {
    expect(isValidMcpUrl('')).toBe(false)
    expect(isValidMcpUrl('   ')).toBe(false)
    expect(isValidMcpUrl('not a url')).toBe(false)
    expect(isValidMcpUrl('mcp.example.com')).toBe(false) // missing scheme
    expect(isValidMcpUrl('ftp://mcp.example.com')).toBe(false) // wrong scheme
  })
})

describe('server id derivation', () => {
  it('slugifies a name', () => {
    expect(slugify('Interactive Brokers')).toBe('interactive-brokers')
    expect(slugify('  Firecrawl!!  ')).toBe('firecrawl')
    expect(slugify('!!!')).toBe('server')
    expect(slugify('')).toBe('server')
  })

  it('collision-suffixes against already-installed ids', () => {
    expect(uniqueServerId('Playwright', [])).toBe('playwright')
    expect(uniqueServerId('Playwright', ['playwright'])).toBe('playwright-2')
    expect(uniqueServerId('Playwright', ['playwright', 'playwright-2'])).toBe('playwright-3')
  })
})

describe('guessServerName — only the fallback when serverInfo.name is absent', () => {
  it('uses the host for a remote server', () => {
    expect(
      guessServerName({ transport: 'remote', url: 'https://mcp.example.com/sse', command: '' }),
    ).toBe('Mcp')
  })

  it('uses the last command segment for a local server', () => {
    expect(guessServerName({ transport: 'local', url: '', command: 'npx -y firecrawl-mcp' })).toBe(
      'Firecrawl Mcp',
    )
  })

  it('falls back to a generic label with nothing to go on', () => {
    expect(guessServerName({ transport: 'local', url: '', command: '' })).toBe('MCP Server')
  })
})
