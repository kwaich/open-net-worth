// @vitest-environment jsdom
//
// Tests for ModelEffortCluster — the model/effort controls that ride in the composer's
// toolbar row (2026-08-06; before that a row of their own above it). Originally: the dock
// followed the side panel's model+effort but exposed no UI for them).

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { ModelEffortCluster } from './ModelEffortRow'

// Override ONLY useModels; keep the real pure helpers (resolveModelOverride,
// effortLevelsFor, clampEffort) so the picker builds a real list.
vi.mock('../../../hooks/useModels', async (importActual) => ({
  ...(await importActual<typeof import('../../../hooks/useModels')>()),
  useModels: () => ({
    models: [
      { id: 'claude-haiku-4-5', provider_key: 'anthropic', provider_label: 'Anthropic' },
      { id: 'grok-4.3', provider_key: 'xai', provider_label: 'xAI (Grok)' },
      // OpenRouter GLM — reasoning can be honestly disabled (can_disable: true).
      {
        id: 'z-ai/glm-5.2',
        provider_key: 'openrouter',
        provider_label: 'OpenRouter',
        effort_levels: ['high', 'xhigh'],
        can_disable: true,
      },
    ],
    source: 'live',
    defaultModel: 'claude-sonnet-5',
    defaultProvider: 'anthropic',
    loaded: true,
  }),
}))

const MSG_WITH_COST = [
  {
    id: '1',
    role: 'assistant',
    content: 'hi',
    usage: { cost_usd: 0.12, input_tokens: 1, output_tokens: 1 },
  },
] as never[]

const sharedEffort = {
  effort: 'medium' as const,
  onEffortChange: vi.fn(),
}

describe('ModelEffortCluster', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })
  afterEach(() => cleanup())

  // The picker opens on a ROOT menu (Model › / Effort ›) — each list is its own pane, so
  // every assertion below navigates to the one it is about first.
  const openRoot = () => fireEvent.click(screen.getByRole('button', { name: 'Chat model' }))
  const openModels = () => {
    openRoot()
    fireEvent.click(screen.getByRole('menuitem', { name: /^Model/ }))
  }
  const openEffort = () => {
    openRoot()
    fireEvent.click(screen.getByRole('menuitem', { name: /^Effort/ }))
  }
  // .mp-opt-name, not textContent: the row also carries a ✓ tick span.
  const tiers = () =>
    screen.getAllByRole('radio').map((r) => ({
      label: r.querySelector('.mp-opt-name')?.textContent,
      on: r.getAttribute('aria-checked') === 'true',
    }))

  // The model control is the shared ui/ModelPicker (a button + popover), not a <select>
  // — see ModelPicker.test.tsx for the list/search/keyboard behaviour itself.
  it('names the model that will actually run, and offers the provider-grouped list', () => {
    render(
      <ModelEffortCluster storedModel="" onModelChange={vi.fn()} messages={[]} {...sharedEffort} />,
    )
    const trigger = screen.getByRole('button', { name: 'Chat model' })
    // Following the instance default still runs a specific model — the trigger names it
    // (and its provider) exactly as it names an explicit pick, never the word "Default".
    expect(trigger.textContent).not.toContain('Default')

    openModels()
    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options.some((o) => o?.includes('claude-haiku-4-5'))).toBe(true)
    expect(options.some((o) => o?.includes('grok-4.3'))).toBe(true)
  })

  it('offers no "Auto" option — automatic model switching was removed', () => {
    render(
      <ModelEffortCluster storedModel="" onModelChange={vi.fn()} messages={[]} {...sharedEffort} />,
    )
    openModels()
    const options = screen.getAllByRole('option').map((o) => o.textContent)
    expect(options).not.toContain('Auto (recommended)')
  })

  it('propagates a model change to the conversation', () => {
    const onChange = vi.fn()
    render(
      <ModelEffortCluster
        storedModel=""
        onModelChange={onChange}
        messages={[]}
        {...sharedEffort}
      />,
    )
    openModels()
    fireEvent.click(screen.getAllByRole('option', { name: /grok-4\.3/ })[0]!)
    expect(onChange).toHaveBeenCalledWith('xai:grok-4.3')
  })

  it('renders the shared effort and publishes changes through the shared setter', () => {
    const onEffortChange = vi.fn()
    render(
      <ModelEffortCluster
        storedModel=""
        onModelChange={vi.fn()}
        messages={[]}
        effort="high"
        onEffortChange={onEffortChange}
      />,
    )
    openEffort()
    expect(tiers().find((t) => t.on)?.label).toBe('High')
    fireEvent.click(screen.getByRole('radio', { name: 'Max' }))
    expect(onEffortChange).toHaveBeenCalledWith('max')
  })

  it('names the selected tier in the trigger, so one control reads "<model> <effort>"', () => {
    render(
      <ModelEffortCluster
        storedModel=""
        onModelChange={vi.fn()}
        messages={[]}
        effort="high"
        onEffortChange={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: 'Chat model' }).textContent).toContain('High')
  })

  it('offers an "Off" option for a model that can disable reasoning, and persists "off"', () => {
    const onEffortChange = vi.fn()
    render(
      <ModelEffortCluster
        storedModel="openrouter:z-ai/glm-5.2"
        onModelChange={vi.fn()}
        messages={[]}
        effort="high"
        onEffortChange={onEffortChange}
      />,
    )
    openEffort()
    expect(tiers().map((t) => t.label)).toEqual(['Off', 'High', 'Xhigh'])
    // Picking a tier applies it AND closes the whole popover, same as picking a model —
    // it must not require an extra outside click/Escape to dismiss.
    fireEvent.click(screen.getByRole('radio', { name: 'Off' }))
    expect(onEffortChange).toHaveBeenCalledWith('off')
    expect(screen.queryByRole('radio')).toBeNull()
  })

  it('does NOT offer "Off" for a model that cannot disable reasoning (clamps a carried "off")', () => {
    // grok-4.3 has no can_disable → a carried "off" must fall to a real tier, not linger.
    const onEffortChange = vi.fn()
    render(
      <ModelEffortCluster
        storedModel="xai:grok-4.3"
        onModelChange={vi.fn()}
        messages={[]}
        effort="off"
        onEffortChange={onEffortChange}
      />,
    )
    openEffort()
    expect(tiers().map((t) => t.label)).not.toContain('Off')
    expect(tiers().find((t) => t.on)?.label).not.toBe('Off')
    expect(onEffortChange).toHaveBeenCalledWith('low')
  })

  it('is ONE control: the cluster holds the model picker, not a second effort dropdown', () => {
    render(
      <ModelEffortCluster storedModel="" onModelChange={vi.fn()} messages={[]} {...sharedEffort} />,
    )
    const row = document.querySelector('.lx-me-cluster')!
    expect(row).not.toBeNull()
    expect(row.querySelector('.lx-me-model')).not.toBeNull()
    expect(row.querySelector('select')).toBeNull()
  })

  it('shows the session cost only when non-zero', () => {
    const { unmount } = render(
      <ModelEffortCluster storedModel="" onModelChange={vi.fn()} messages={[]} {...sharedEffort} />,
    )
    expect(document.querySelector('.chat-session-cost')).toBeNull()
    unmount()
    render(
      <ModelEffortCluster
        storedModel=""
        onModelChange={vi.fn()}
        messages={MSG_WITH_COST}
        {...sharedEffort}
      />,
    )
    expect(document.querySelector('.chat-session-cost')).not.toBeNull()
  })
})
