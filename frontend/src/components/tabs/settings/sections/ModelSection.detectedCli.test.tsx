// @vitest-environment jsdom
//
// Settings → AI: a locally-detected CLI must render as a provider row.
//
// A CLI transport enrols by DETECTION, not by stored credentials — it has no api_key or
// base_url to save, so it never appears in `configured_providers`. The list filtered on that
// map alone, so a machine with the Claude Code CLI installed, selected as the instance
// default, and already supplying every model picker still showed "No providers yet".
//
// Fixing the filter then crashed the panel outright: the row was handed
// `cfg={configured[key]!}` — a non-null assertion that was only true while the list was
// filtered to configured entries — and `cfg.enabled` threw "Cannot read properties of
// undefined (reading 'enabled')", taking the whole Settings tab down. `tsc` cannot see past a
// `!`, so only a render catches it. That is what this file is for.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'

vi.mock('../../../../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))
vi.mock('../../../../hooks/useModels', () => ({
  MODEL_CONFIG_CHANGED_EVENT: 'model-config-changed',
  modelRejectsImages: () => false,
  useModels: () => ({ models: [], defaultModel: 'claude_cli:sonnet', loading: false }),
}))

import { ModelSection } from './ModelSection'
import { authFetch } from '../../../../api/client'
import { invalidateQueryCache } from '../../../../state/queryCache'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

// The exact shape a demo box reports: the CLI is detected, nothing is configured.
const PROVIDERS = {
  catalog: [
    {
      key: 'claude_cli',
      label: 'Claude Code CLI',
      transport: 'claude_cli',
      base_url: '',
      needs_api_key: false,
      editable_base_url: false,
      model_hints: [],
      cli_binary: 'claude',
    },
    {
      key: 'anthropic',
      label: 'Anthropic',
      transport: 'anthropic',
      base_url: '',
      needs_api_key: true,
      editable_base_url: false,
      model_hints: [],
    },
  ],
  configured_providers: {},
  detected_clis: { claude_cli: true },
  detected_cli_paths: { claude_cli: '/Users/someone/.local/bin/claude' },
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function route(url: string): Response {
  if (url.startsWith('/api/model-config/providers')) return json(PROVIDERS)
  if (url.startsWith('/api/model-config'))
    return json({
      default_model: 'claude_cli:sonnet',
      provider: 'claude_cli',
      model: 'sonnet',
      base_url: '',
      task_models: {},
    })
  if (url.startsWith('/api/preferred-models')) return json({ models: [] })
  if (url.startsWith('/api/memory/settings')) return json({ enabled: true })
  if (url.startsWith('/api/memory/facts')) return json({ total: 0 })
  if (url.startsWith('/api/cost/summary'))
    return json({
      total: { turns: 0, input_tokens: 0, cache_read_tokens: 0, output_tokens: 0, cost_usd: 0 },
      by_model: {},
      by_provider: {},
      by_surface: {},
      window_days: 1,
    })
  return json({})
}

beforeEach(() => {
  vi.clearAllMocks()
  invalidateQueryCache(() => true)
  mockAuthFetch.mockImplementation(async (url: string) => route(String(url)))
})
afterEach(() => cleanup())

describe('ModelSection — a detected CLI with nothing configured', () => {
  it('renders the CLI as a provider row instead of "No providers yet"', async () => {
    render(<ModelSection toast={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Claude Code CLI')).toBeTruthy())
    expect(screen.queryByText('No providers yet')).toBeNull()
  })

  it('does not crash reading config off a provider that has none', async () => {
    // The regression, stated directly: `cfg.enabled` on an undefined cfg.
    const errors: unknown[] = []
    const spy = vi.spyOn(console, 'error').mockImplementation((...a) => errors.push(a))
    try {
      render(<ModelSection toast={vi.fn()} />)
      await waitFor(() => expect(screen.getByText('Claude Code CLI')).toBeTruthy())
      const text = errors.map((e) => String(e)).join(' ')
      expect(text).not.toMatch(/reading 'enabled'/)
      expect(text).not.toMatch(/Cannot read properties of undefined/)
    } finally {
      spy.mockRestore()
    }
  })

  it('shows where the binary was found, so the row says something true', async () => {
    render(<ModelSection toast={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Claude Code CLI')).toBeTruthy())
    expect(screen.getByText('/Users/someone/.local/bin/claude')).toBeTruthy()
  })
})
