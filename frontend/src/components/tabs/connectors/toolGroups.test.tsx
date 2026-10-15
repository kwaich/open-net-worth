// @vitest-environment jsdom
//
// The ladder's two headings follow the backend's per-tool `read_only` flag — an MCP server's
// own `readOnlyHint` when it advertises one — and fall back to the name-verb guess only when
// the flag is absent. QA 2026-10-11: a server annotating `echo`/`add_numbers` read-only had
// all three of its tools filed under "Write & delete tools", on the detail page and in the
// add flow alike, because neither name contains a read verb.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { renderHook, cleanup, act } from '@testing-library/react'

vi.mock('./mcpApi', async (importOriginal) => {
  const real = await importOriginal<typeof import('./mcpApi')>()
  return {
    ...real,
    probeMcpServer: vi.fn(),
    fetchProbeProgress: vi.fn(async () => ({ running: false, elapsed: 0, lines: [] })),
  }
})

import { READ_GROUP, WRITE_GROUP, buildToolGroups } from './model'
import { toEntry } from './adapter'
import { useMcpAddFlow } from './McpAddFlow'
import { probeMcpServer } from './mcpApi'
import type { Connector } from './types'

afterEach(cleanup)

const ANNOTATED = [
  { name: 'echo', read_only: true },
  { name: 'add_numbers', read_only: true },
  { name: 'write_note', read_only: false },
]
const EXPECTED = [
  { name: READ_GROUP, tools: ['echo', 'add_numbers'] },
  { name: WRITE_GROUP, tools: ['write_note'] },
]

describe('buildToolGroups', () => {
  it('files a tool by its read_only flag even when its name has no read verb', () => {
    expect(buildToolGroups(ANNOTATED)).toEqual(EXPECTED)
  })

  it('lets read_only=false override a read-looking name', () => {
    expect(buildToolGroups([{ name: 'get_and_delete', read_only: false }])).toEqual([
      { name: WRITE_GROUP, tools: ['get_and_delete'] },
    ])
  })

  it('falls back to the verb guess when the flag is absent', () => {
    expect(buildToolGroups(['list_files', { name: 'search_web' }, 'echo'])).toEqual([
      { name: READ_GROUP, tools: ['list_files', 'search_web'] },
      { name: WRITE_GROUP, tools: ['echo'] },
    ])
  })
})

describe('connector detail entry', () => {
  it('groups an MCP connector by the wire read_only flags', () => {
    const c: Connector = {
      id: 'mcp:qa-server',
      name: 'QA server',
      kind: 'mcp',
      enabled: true,
      config: {},
      secrets_public: {},
      status: 'connected',
      pausable: true,
      short_description: '',
      tools: ANNOTATED.map((t) => ({ ...t, description: '', source: 'mcp' })),
    } as Connector
    expect(toEntry(c).toolGroups).toEqual(EXPECTED)
  })
})

describe('add flow permission step', () => {
  it('groups probed tools by their read_only flags', async () => {
    vi.mocked(probeMcpServer).mockResolvedValue({
      ok: true,
      server_info: { name: 'QA server', version: '1' },
      tools: ANNOTATED,
      suggested_name: 'QA server',
      suggested_id: 'qa-server',
      trust: null,
      error: null,
    })
    const { result } = renderHook(() => useMcpAddFlow({ takenIds: [], onSaved: () => {} }))
    act(() => {
      result.current.setForm((f) => ({ ...f, transport: 'local', command: 'npx' }))
    })
    await act(async () => {
      result.current.runConnect()
    })
    expect(result.current.tools).toEqual(['echo', 'add_numbers', 'write_note'])
    expect(result.current.toolGroups).toEqual(EXPECTED)
  })
})
