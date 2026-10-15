// @vitest-environment jsdom
//
// Settings → AI Model: removing a configured provider from the Edit provider modal.
//
// This flow had no test, and it broke twice in a row unnoticed. First it called the SIBLING
// endpoint `…/providers/{key}/key`, which drops only the API key and leaves the entry at
// `enabled: false` — so pressing Remove appeared to do nothing and the provider stayed in the
// list forever. Then `removeProvider` was deleted outright as collateral in a neighbouring
// rewrite (`5abdb183`), leaving `onRemove` wired to a name that no longer existed; only
// `tsc` could see it, and the frontend typecheck was already red for other reasons.
//
// So this pins the two things that actually failed: the button reaches the server at all, and
// it reaches the endpoint that removes the whole provider rather than just its key.
//
// The pre-redesign settings `ProviderRow` also had a separate "Remove key" button calling
// `/key`; that component is gone, so nothing in Settings calls `/key` today. If one comes
// back, it must stay a separate control — the two must not be conflated again.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../../../../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))
vi.mock('../../../../hooks/useModels', () => ({
  MODEL_CONFIG_CHANGED_EVENT: 'model-config-changed',
  modelRejectsImages: () => false,
  useModels: () => ({ models: [], defaultModel: 'anthropic:claude-haiku-4-5', loading: false }),
}))

import { ModelSection } from './ModelSection'
import { authFetch } from '../../../../api/client'
import { invalidateQueryCache } from '../../../../state/queryCache'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const PROVIDERS = {
  catalog: [
    {
      key: 'anthropic',
      label: 'Anthropic',
      transport: 'anthropic',
      base_url: '',
      needs_api_key: true,
      editable_base_url: false,
      model_hints: [],
    },
    {
      key: 'openai',
      label: 'OpenAI',
      transport: 'openai_compatible',
      base_url: 'https://api.openai.com/v1',
      needs_api_key: true,
      editable_base_url: false,
      model_hints: [],
    },
  ],
  // `openai` is deliberately NOT the instance default below — the modal hides Remove on the
  // default provider, so a default-backed row could never exercise this path.
  configured_providers: {
    anthropic: { has_api_key: true, base_url: '', model: 'claude-haiku-4-5', enabled: true },
    openai: {
      has_api_key: true,
      base_url: 'https://api.openai.com/v1',
      model: 'gpt-4o-mini',
      enabled: true,
    },
  },
  detected_clis: {},
  detected_cli_paths: {},
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
      default_model: 'anthropic:claude-haiku-4-5',
      provider: 'anthropic',
      model: 'claude-haiku-4-5',
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

function deleteCalls() {
  return mockAuthFetch.mock.calls.filter(
    (c) => (c[1] as RequestInit | undefined)?.method === 'DELETE',
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  invalidateQueryCache(() => true)
  mockAuthFetch.mockImplementation(async (url: string) => route(String(url)))
})
afterEach(() => cleanup())

// Every provider row's Edit button is labelled just "Edit", so scope the query to the row
// carrying the provider's name rather than indexing into the list by position.
async function openEditorFor(label: string) {
  await waitFor(() => expect(screen.getByText(label)).toBeTruthy())
  const row = screen.getByText(label).closest('.rd-prov')
  expect(row).toBeTruthy()
  fireEvent.click(within(row as HTMLElement).getByRole('button', { name: 'Edit' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Remove' })).toBeTruthy())
}

describe('ModelSection — removing a provider', () => {
  it('DELETEs the provider itself, not just its key', async () => {
    render(<ModelSection toast={vi.fn()} />)
    await openEditorFor('OpenAI')

    // First press only arms the confirm.
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(deleteCalls()).toHaveLength(0)

    fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }))

    await waitFor(() => expect(deleteCalls()).toHaveLength(1))
    const [url] = deleteCalls()[0]!
    expect(url).toBe('/api/model-config/providers/openai')
    expect(String(url).endsWith('/key')).toBe(false)
  })

  it('surfaces the server’s refusal instead of reporting success', async () => {
    const toast = vi.fn()
    mockAuthFetch.mockImplementation(async (url: string, init?: RequestInit) => {
      if (init?.method === 'DELETE')
        return json({ detail: 'Cannot remove — it is the active default.' }, 409)
      return route(String(url))
    })

    render(<ModelSection toast={toast} />)
    await openEditorFor('OpenAI')
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm remove' }))

    await waitFor(() =>
      expect(toast).toHaveBeenCalledWith('Cannot remove — it is the active default.'),
    )
    expect(toast).not.toHaveBeenCalledWith('Provider removed')
  })
})
