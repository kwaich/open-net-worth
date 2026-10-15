// @vitest-environment jsdom
//
// Settings → AI usage → Model performance panel (ModelTelemetryPanel.tsx), mounted through
// the real section that owns both usage panels.
//
// Covers: rows from mock API data, the empty state (AC 4), the error state + retry (AC 14),
// the partial-data em-dash for a null duration_ms_p50 (AC 2 / R4), the provider-label vs.
// bare-id fallback (AC 13), and the JSON-retry badge tone thresholds (D1).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../../../../api/client', () => ({
  authFetch: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}))
vi.mock('../../../../hooks/useModels', () => ({
  MODEL_CONFIG_CHANGED_EVENT: 'model-config-changed',
  modelRejectsImages: () => false,
  useModels: () => ({ models: [], defaultModel: 'claude_cli:sonnet', loading: false }),
}))

import { AiUsageSection } from './AiUsageSection'
import { authFetch } from '../../../../api/client'
import { invalidateQueryCache } from '../../../../state/queryCache'
import type { ModelTelemetryRow } from './ModelTelemetryPanel'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

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
  ],
  configured_providers: {},
  detected_clis: { claude_cli: true },
  detected_cli_paths: {},
}

const COST_SUMMARY = {
  total: { turns: 0, input_tokens: 0, cache_read_tokens: 0, output_tokens: 0, cost_usd: 0 },
  by_model: {},
  by_provider: {},
  by_search_fetch: {},
  by_surface: {},
  window_days: 1,
}

function baseRoute(url: string): Response | null {
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
  if (url.startsWith('/api/cost/summary')) return json(COST_SUMMARY)
  return null
}

function row(overrides: Partial<ModelTelemetryRow>): ModelTelemetryRow {
  return {
    model: 'sonnet',
    provider: 'Claude Code CLI',
    ttft_ms_p50: 210,
    duration_ms_p50: 1800,
    tok_per_sec_p50: 42.5,
    error_rate: 0,
    errors: 0,
    samples: 12,
    ...overrides,
  }
}

function mockTelemetry(models: ModelTelemetryRow[] | null, status = 200) {
  mockAuthFetch.mockImplementation(async (url: string) => {
    const u = String(url)
    if (u.startsWith('/api/ai/model-telemetry')) {
      if (models === null) return json({ detail: 'boom' }, status)
      return json({ models })
    }
    return baseRoute(u) ?? json({})
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  invalidateQueryCache(() => true)
})
afterEach(() => cleanup())

describe('ModelTelemetryPanel — rows', () => {
  it('presents model metrics as one semantic comparison table with named columns', async () => {
    mockTelemetry([row({ model: 'sonnet', provider: 'Claude Code CLI' })])
    render(<AiUsageSection toast={vi.fn()} />)

    const table = await screen.findByRole('table', { name: 'Model performance comparison' })
    expect(table).toBeTruthy()
    expect(screen.getAllByRole('columnheader').map((heading) => heading.textContent)).toEqual([
      'Model',
      'TTFT p50',
      'Duration p50',
      'Tok/sec p50',
      'JSON-retry rate',
    ])
  })

  it('renders a row per model with its latency stats and JSON-retry badge', async () => {
    mockTelemetry([row({ model: 'sonnet', provider: 'Claude Code CLI' })])
    render(<AiUsageSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByText('Model performance')).toBeTruthy())
    expect(screen.getByText('Claude Code CLI — sonnet')).toBeTruthy()
    expect(screen.getByText('210 ms')).toBeTruthy()
    expect(screen.getByText('1.8 s')).toBeTruthy()
    expect(screen.getByText('42.5 tok/s')).toBeTruthy()
    expect(screen.getByText('12 samples')).toBeTruthy()
  })

  it('falls back to the bare model id — never the literal string "Unknown" — when the catalog does not know the provider (AC 13)', async () => {
    mockTelemetry([row({ model: 'some-local-override', provider: null })])
    render(<AiUsageSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByText('some-local-override')).toBeTruthy())
    expect(screen.queryByText(/Unknown/)).toBeNull()
  })

  it('renders an em dash with an explanatory title for a null duration_ms_p50, leaving the other two tiles normal (AC 2 / R4)', async () => {
    mockTelemetry([row({ duration_ms_p50: null })])
    render(<AiUsageSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByText('Model performance')).toBeTruthy())
    expect(screen.getByText('210 ms')).toBeTruthy() // TTFT tile unaffected
    expect(screen.getByText('42.5 tok/s')).toBeTruthy() // tok/sec tile unaffected
    const dash = screen.getByText('–')
    expect(dash.getAttribute('title')).toMatch(/predate/)
  })
})

describe('ModelTelemetryPanel — JSON-retry badge tone (D1)', () => {
  it('reads good at a zero rate and leads with the count and its denominator, not a bare percentage', async () => {
    mockTelemetry([row({ errors: 0, samples: 47, error_rate: 0 })])
    render(<AiUsageSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByText(/0 of 47/)).toBeTruthy())
    const badge = screen.getByText(/0 of 47/)
    expect(badge.className).toContain('good')
  })

  it('reads warn between 0 and 10%', async () => {
    mockTelemetry([row({ errors: 3, samples: 47, error_rate: 3 / 50 })])
    render(<AiUsageSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByText(/3 of 50/)).toBeTruthy())
    expect(screen.getByText(/3 of 50/).className).toContain('warn')
  })

  it('reads bad at or above 10%', async () => {
    mockTelemetry([row({ errors: 6, samples: 44, error_rate: 6 / 50 })])
    render(<AiUsageSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByText(/6 of 50/)).toBeTruthy())
    expect(screen.getByText(/6 of 50/).className).toContain('bad')
  })

  it('renders "<1%" rather than collapsing a small nonzero rate into "no retries"', async () => {
    mockTelemetry([row({ errors: 1, samples: 199, error_rate: 1 / 200 })])
    render(<AiUsageSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByText(/<1%/)).toBeTruthy())
  })
})

describe('ModelTelemetryPanel — empty state (AC 4)', () => {
  it('shows an explicit "no telemetry yet" message on a fresh instance', async () => {
    mockTelemetry([])
    render(<AiUsageSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByText('No telemetry yet')).toBeTruthy())
  })
})

describe('ModelTelemetryPanel — error state + retry (AC 14)', () => {
  it('shows an error state with a working retry affordance, not a blank panel', async () => {
    mockTelemetry(null, 500)
    render(<AiUsageSection toast={vi.fn()} />)

    await waitFor(() => expect(screen.getByText('Could not load model performance')).toBeTruthy())

    mockTelemetry([row({ model: 'haiku', provider: 'Claude Code CLI' })])
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText('Claude Code CLI — haiku')).toBeTruthy())
  })
})
