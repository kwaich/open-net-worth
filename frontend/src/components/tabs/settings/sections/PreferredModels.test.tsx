// @vitest-environment jsdom
//
// Tests for the "Preferred models" wrapper (Settings → AI Models). Since the picker
// unification it is only draft state + an explicit Save around the shared
// ui/ModelPicker in `multiple` mode — the list, search, grouping and chip behaviour
// are ModelPicker's and are tested in ui/ModelPicker.test.tsx.
//
// What is pinned HERE is the part that is this component's own: the saved list arrives
// async, so the draft must re-sync when it lands, and Save must not fire until the
// draft actually differs from what is stored.
//
// The shortlist orders pickers and selects nothing: there is deliberately no assertion
// about which model runs a turn, because it no longer has any say in that.

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ModelOption } from '../../../../api/models'
import { PreferredModels } from './ModelSection'

const MODELS: ModelOption[] = [
  { id: 'claude-haiku-4-5', provider_key: 'anthropic', provider_label: 'Anthropic' },
  { id: 'gpt-4o-mini', provider_key: 'openai', provider_label: 'OpenAI' },
  { id: 'grok-4.5', provider_key: 'xai', provider_label: 'xAI' },
] as ModelOption[]

const save = () => screen.getByRole('button', { name: 'Save preferred models' })
const openPicker = () => fireEvent.click(screen.getByRole('button', { name: 'Preferred models' }))

afterEach(() => cleanup())

describe('PreferredModels', () => {
  it('renders a chip per saved ref, bare id plus provider label', () => {
    render(
      <PreferredModels models={MODELS} saved={['anthropic:claude-haiku-4-5']} onSave={vi.fn()} />,
    )
    expect(screen.getByText('claude-haiku-4-5')).toBeTruthy()
    expect(screen.getByText('Anthropic')).toBeTruthy()
  })

  it('shows the empty hint when nothing is picked', () => {
    render(<PreferredModels models={MODELS} saved={[]} onSave={vi.fn()} />)
    expect(screen.getByText(/every available model shows in the picker/)).toBeTruthy()
  })

  it('re-syncs the draft when the saved list arrives after mount (async fetch)', () => {
    const { rerender } = render(<PreferredModels models={MODELS} saved={[]} onSave={vi.fn()} />)
    expect(screen.queryByText('gpt-4o-mini')).toBeNull()

    rerender(<PreferredModels models={MODELS} saved={['openai:gpt-4o-mini']} onSave={vi.fn()} />)
    expect(screen.getByText('gpt-4o-mini')).toBeTruthy()
    // A landed fetch is not an edit — Save must not light up.
    expect((save() as HTMLButtonElement).disabled).toBe(true)
  })

  it('Save is disabled until the draft differs from what is saved', () => {
    render(<PreferredModels models={MODELS} saved={['xai:grok-4.5']} onSave={vi.fn()} />)
    expect((save() as HTMLButtonElement).disabled).toBe(true)

    fireEvent.click(screen.getByLabelText('Remove xai:grok-4.5'))
    expect((save() as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText('Unsaved')).toBeTruthy()
  })

  it('adding through the picker and saving submits provider-pinned refs', () => {
    const onSave = vi.fn()
    render(<PreferredModels models={MODELS} saved={['xai:grok-4.5']} onSave={onSave} />)

    openPicker()
    fireEvent.click(screen.getAllByRole('option', { name: /gpt-4o-mini/ })[0]!)
    fireEvent.click(save())

    expect(onSave).toHaveBeenCalledWith(['xai:grok-4.5', 'openai:gpt-4o-mini'])
  })

  it('removing a chip and saving submits the shortened list', () => {
    const onSave = vi.fn()
    render(
      <PreferredModels
        models={MODELS}
        saved={['xai:grok-4.5', 'openai:gpt-4o-mini']}
        onSave={onSave}
      />,
    )
    fireEvent.click(screen.getByLabelText('Remove xai:grok-4.5'))
    fireEvent.click(save())
    expect(onSave).toHaveBeenCalledWith(['openai:gpt-4o-mini'])
  })

  it('never claims a save itself — the toast belongs to the caller, which knows if the PUT worked', () => {
    const onSave = vi.fn()
    render(<PreferredModels models={MODELS} saved={[]} onSave={onSave} />)

    openPicker()
    fireEvent.click(screen.getAllByRole('option', { name: /claude-haiku-4-5/ })[0]!)
    fireEvent.click(save())

    expect(onSave).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Preferred models saved')).toBeNull()
  })

  it('does not save on every pick — a shortlist is edited a few models at a time', () => {
    const onSave = vi.fn()
    render(<PreferredModels models={MODELS} saved={[]} onSave={onSave} />)

    openPicker()
    fireEvent.click(screen.getAllByRole('option', { name: /claude-haiku-4-5/ })[0]!)
    fireEvent.click(screen.getAllByRole('option', { name: /gpt-4o-mini/ })[0]!)

    expect(onSave).not.toHaveBeenCalled()
    fireEvent.click(save())
    expect(onSave).toHaveBeenCalledWith(['anthropic:claude-haiku-4-5', 'openai:gpt-4o-mini'])
  })
})
