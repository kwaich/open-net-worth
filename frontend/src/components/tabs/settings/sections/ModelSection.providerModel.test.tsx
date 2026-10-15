// @vitest-environment jsdom
//
// Settings → AI Model → Edit provider: the "Default model" field.
//
// It was a native <input list> over the catalog's FOUR hard-coded hints, so on OpenRouter
// (~470 live models) typing "glm" matched nothing and the field looked unsearchable. It now
// searches the provider's own live list through the shared ModelPicker, and still accepts a
// typed id the list does not contain.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'

vi.mock('../../../../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))

const LIVE_MODELS = [
  { id: 'z-ai/glm-5.3-flash', provider_key: 'openrouter', provider_label: 'OpenRouter' },
  { id: 'z-ai/glm-5.3', provider_key: 'openrouter', provider_label: 'OpenRouter' },
  { id: 'openai/gpt-5', provider_key: 'openrouter', provider_label: 'OpenRouter' },
  { id: 'claude-haiku-4-5', provider_key: 'anthropic', provider_label: 'Anthropic' },
]
let liveModels = LIVE_MODELS
vi.mock('../../../../hooks/useModels', () => ({
  MODEL_CONFIG_CHANGED_EVENT: 'model-config-changed',
  modelRejectsImages: () => false,
  useModels: () => ({
    models: liveModels,
    defaultModel: 'anthropic:claude-haiku-4-5',
    loading: false,
  }),
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
      key: 'openrouter',
      label: 'OpenRouter',
      transport: 'openai_compatible',
      base_url: 'https://openrouter.ai/api/v1',
      needs_api_key: true,
      editable_base_url: false,
      model_hints: ['anthropic/claude-opus-5', 'openai/gpt-5'],
    },
    {
      key: 'custom',
      label: 'Custom',
      transport: 'openai_compatible',
      base_url: '',
      needs_api_key: true,
      editable_base_url: true,
      model_hints: [],
    },
  ],
  configured_providers: {
    anthropic: { has_api_key: true, base_url: '', model: 'claude-haiku-4-5', enabled: true },
    openrouter: {
      has_api_key: true,
      base_url: 'https://openrouter.ai/api/v1',
      model: '',
      enabled: true,
    },
    custom: {
      has_api_key: true,
      base_url: 'https://llm.example.test/v1',
      model: '',
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

beforeEach(() => {
  vi.clearAllMocks()
  liveModels = LIVE_MODELS
  invalidateQueryCache(() => true)
  mockAuthFetch.mockImplementation(async (url: string) => route(String(url)))
})
afterEach(() => cleanup())

async function openEditorFor(label: string) {
  await waitFor(() => expect(screen.getByText(label)).toBeTruthy())
  const row = screen.getByText(label).closest('.rd-prov')
  expect(row).toBeTruthy()
  fireEvent.click(within(row as HTMLElement).getByRole('button', { name: 'Edit' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Remove' })).toBeTruthy())
}

const modelField = () => screen.getByRole('button', { name: 'Default model for OpenRouter' })
const search = (value: string) =>
  fireEvent.change(screen.getByLabelText('Search models'), { target: { value } })

function saved() {
  const call = mockAuthFetch.mock.calls.find(
    (c) =>
      String(c[0]) === '/api/model-config/providers/openrouter' &&
      (c[1] as RequestInit | undefined)?.method === 'PUT',
  )
  return call ? JSON.parse(String((call[1] as RequestInit).body)) : null
}

describe('Edit provider — Default model', () => {
  it('searches the provider’s live models, not four hard-coded hints', async () => {
    render(<ModelSection toast={vi.fn()} />)
    await openEditorFor('OpenRouter')

    fireEvent.click(modelField())
    search('glm')

    const names = screen.getAllByRole('option').map((o) => o.textContent)
    expect(names.some((n) => n?.includes('z-ai/glm-5.3-flash'))).toBe(true)
    expect(names.some((n) => n?.includes('z-ai/glm-5.3'))).toBe(true)
    // Only THIS provider's models — another provider's never leak into the list.
    expect(names.some((n) => n?.includes('claude-haiku-4-5'))).toBe(false)
  })

  it('saves the picked model as a bare id (no provider prefix)', async () => {
    render(<ModelSection toast={vi.fn()} />)
    await openEditorFor('OpenRouter')

    fireEvent.click(modelField())
    search('glm-5.3-flash')
    fireEvent.click(screen.getAllByRole('option', { name: /z-ai\/glm-5\.3-flash/ })[0]!)
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(saved()).not.toBeNull())
    expect(saved().model).toBe('z-ai/glm-5.3-flash')
  })

  it('still accepts a typed id the list does not contain, colon suffix intact', async () => {
    render(<ModelSection toast={vi.fn()} />)
    await openEditorFor('OpenRouter')

    fireEvent.click(modelField())
    search('meta-llama/llama-3.3-70b-instruct:free')
    fireEvent.click(screen.getAllByRole('option')[0]!)
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))

    await waitFor(() => expect(saved()).not.toBeNull())
    expect(saved().model).toBe('meta-llama/llama-3.3-70b-instruct:free')
  })

  it('falls back to the catalog hints when no live list is known yet', async () => {
    liveModels = []
    render(<ModelSection toast={vi.fn()} />)
    await openEditorFor('OpenRouter')

    fireEvent.click(modelField())

    const names = screen.getAllByRole('option').map((o) => o.textContent)
    expect(names.some((n) => n?.includes('anthropic/claude-opus-5'))).toBe(true)
    expect(names.some((n) => n?.includes('openai/gpt-5'))).toBe(true)
  })

  it('shows the placeholder while no model is set', async () => {
    render(<ModelSection toast={vi.fn()} />)
    await openEditorFor('OpenRouter')

    expect(modelField().textContent).toContain('Choose a model…')
  })
})
