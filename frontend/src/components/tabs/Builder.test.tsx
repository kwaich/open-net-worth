// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Builder } from './Builder'

vi.mock('../../api/builder', () => ({
  getBuilderStatus: vi.fn(),
  startBuilderRun: vi.fn(),
  getBuilderRun: vi.fn(),
  stopBuilderRun: vi.fn(),
}))

import { getBuilderRun, getBuilderStatus, startBuilderRun, stopBuilderRun } from '../../api/builder'

const mockStatus = getBuilderStatus as ReturnType<typeof vi.fn>
const mockStart = startBuilderRun as ReturnType<typeof vi.fn>
const mockGetRun = getBuilderRun as ReturnType<typeof vi.fn>
const mockStop = stopBuilderRun as ReturnType<typeof vi.fn>

const STATUS = {
  workspace: '/repo/open-net-worth',
  workspace_name: 'open-net-worth',
  workspace_valid: true,
  error: '',
  active_run_id: null,
  engines: [
    {
      id: 'codex',
      label: 'Codex',
      installed: true,
      version: 'codex-cli 0.145.0',
      command: '/opt/bin/codex',
      build_is_sandboxed: true,
      guidance: '',
    },
    {
      id: 'claude',
      label: 'Claude Code',
      installed: false,
      version: '',
      command: 'claude',
      build_is_sandboxed: false,
      guidance: 'Install Claude Code and run claude login.',
    },
  ],
}

const RUNNING = {
  id: 'build_1',
  engine: 'codex',
  mode: 'build',
  prompt: 'Add a dashboard card',
  status: 'running',
  events: [{ seq: 1, kind: 'status', text: 'Codex started.', created_at: '2026-07-23T00:00:00Z' }],
  started_at: '2026-07-23T00:00:00Z',
  finished_at: null,
  error: '',
  workspace_changes: [],
}

describe('Builder', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStatus.mockResolvedValue(STATUS)
  })

  afterEach(() => cleanup())

  it('renders engine readiness and setup guidance without pretending a missing engine works', async () => {
    render(<Builder />)

    await waitFor(() => expect(screen.getByText('Build Open Net Worth')).toBeTruthy())
    expect(screen.getByText('codex-cli 0.145.0')).toBeTruthy()
    expect(screen.getByText('Not installed')).toBeTruthy()
    expect(screen.getByText(/Install Claude Code and run claude login/)).toBeTruthy()
    expect(screen.getByText('/repo/open-net-worth')).toBeTruthy()
  })

  it('turns an all-missing engine state into a guided setup path with recheck', async () => {
    mockStatus.mockResolvedValue({
      ...STATUS,
      engines: STATUS.engines.map((item) => ({
        ...item,
        installed: false,
        version: '',
      })),
    })
    render(<Builder />)

    await waitFor(() => expect(screen.getByText('Set up a coding engine')).toBeTruthy())
    expect(screen.getByText(/install one engine, sign in locally/i)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Check again' }))

    await waitFor(() => expect(mockStatus).toHaveBeenCalledTimes(2))
  })

  it('makes Plan and Build authority explicit', async () => {
    render(<Builder />)
    await waitFor(() => screen.getByRole('tab', { name: 'Plan' }))

    expect(screen.getByText(/reads the app and recommends an approach/i)).toBeTruthy()
    expect(screen.getByText('How far should it go?')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Plan this change' })).toBeTruthy()

    fireEvent.click(screen.getByRole('tab', { name: 'Build' }))

    expect(screen.getByText(/can edit files and run local commands/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Build this change' })).toBeTruthy()
  })

  it('confirms the fixed local boundary immediately before Build starts', async () => {
    mockStart.mockResolvedValue(RUNNING)
    render(<Builder />)
    await waitFor(() => screen.getByLabelText('What should Open Net Worth change?'))

    fireEvent.change(screen.getByLabelText('What should Open Net Worth change?'), {
      target: { value: 'Add a dashboard card' },
    })
    fireEvent.click(screen.getByRole('tab', { name: 'Build' }))
    fireEvent.click(screen.getByRole('button', { name: 'Build this change' }))

    expect(mockStart).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Let Codex edit Open Net Worth?' })).toBeTruthy()
    expect(
      screen.getByText(/is instructed not to commit, push, open a pull request, or deploy/i),
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Start building' }))
    await waitFor(() =>
      expect(mockStart).toHaveBeenCalledWith({
        engine: 'codex',
        mode: 'build',
        prompt: 'Add a dashboard card',
      }),
    )
  })

  it('inserts a plain-language starter into the request field', async () => {
    render(<Builder />)
    await waitFor(() => screen.getByRole('button', { name: /Add something new/i }))

    fireEvent.click(screen.getByRole('button', { name: /Add something new/i }))

    const request = screen.getByLabelText(
      'What should Open Net Worth change?',
    ) as HTMLTextAreaElement
    expect(request.value).toMatch(/Add a new dashboard card/)
  })

  it('starts with the selected engine/mode, polls, and shows workspace changes', async () => {
    mockStart.mockResolvedValue(RUNNING)
    mockGetRun.mockResolvedValue({
      ...RUNNING,
      status: 'succeeded',
      finished_at: '2026-07-23T00:02:00Z',
      events: [
        {
          seq: 2,
          kind: 'assistant',
          text: 'The dashboard card is ready.',
          created_at: '2026-07-23T00:02:00Z',
        },
      ],
      workspace_changes: ['M frontend/src/dashboard.tsx'],
    })
    render(<Builder pollIntervalMs={5} />)
    await waitFor(() => screen.getByLabelText('What should Open Net Worth change?'))

    fireEvent.change(screen.getByLabelText('What should Open Net Worth change?'), {
      target: { value: 'Add a dashboard card' },
    })
    fireEvent.click(screen.getByRole('tab', { name: 'Build' }))
    fireEvent.click(screen.getByRole('button', { name: 'Build this change' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start building' }))

    await waitFor(() => {
      expect(mockStart).toHaveBeenCalledWith({
        engine: 'codex',
        mode: 'build',
        prompt: 'Add a dashboard card',
      })
    })
    await waitFor(() =>
      expect(screen.getAllByText('The dashboard card is ready.').length).toBeGreaterThan(0),
    )
    expect(screen.getByText('Change ready to review')).toBeTruthy()
    expect(screen.getByText('M frontend/src/dashboard.tsx')).toBeTruthy()
    expect(screen.getByText('Finished')).toBeTruthy()
  })

  it('keeps technical engine output behind an optional activity detail', async () => {
    mockStart.mockResolvedValue({
      ...RUNNING,
      mode: 'plan',
      events: [
        ...RUNNING.events,
        {
          seq: 2,
          kind: 'tool',
          text: 'Read frontend/src/App.tsx',
          created_at: '2026-07-23T00:00:01Z',
        },
        {
          seq: 3,
          kind: 'output',
          text: 'Found the navigation configuration.',
          created_at: '2026-07-23T00:00:02Z',
        },
      ],
    })
    render(<Builder />)
    await waitFor(() => screen.getByLabelText('What should Open Net Worth change?'))

    fireEvent.change(screen.getByLabelText('What should Open Net Worth change?'), {
      target: { value: 'Plan a dashboard card' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Plan this change' }))

    await waitFor(() => screen.getByText('Codex started.'))
    const technical = screen.getByText('Show technical activity (2)').closest('details')
    expect(technical).toBeTruthy()
    expect(within(technical!).getByText('Read frontend/src/App.tsx')).toBeTruthy()
    expect(within(technical!).getByText('Found the navigation configuration.')).toBeTruthy()
  })

  it('stops a running engine from the visible activity surface', async () => {
    mockStart.mockResolvedValue(RUNNING)
    mockGetRun.mockImplementation(
      (_id, _after, signal) =>
        new Promise((_resolve, reject) => {
          signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
        }),
    )
    mockStop.mockResolvedValue({ ...RUNNING, status: 'cancelled', events: [] })
    render(<Builder pollIntervalMs={5} />)
    await waitFor(() => screen.getByLabelText('What should Open Net Worth change?'))

    fireEvent.change(screen.getByLabelText('What should Open Net Worth change?'), {
      target: { value: 'Change the theme' },
    })
    fireEvent.click(screen.getByRole('tab', { name: 'Build' }))
    fireEvent.click(screen.getByRole('button', { name: 'Build this change' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start building' }))
    await waitFor(() => screen.getByRole('button', { name: 'Stop run' }))
    fireEvent.click(screen.getByRole('button', { name: 'Stop run' }))

    await waitFor(() => expect(mockStop).toHaveBeenCalledWith('build_1'))
    expect(screen.getByText('Stopped')).toBeTruthy()
  })

  it('renders a retryable readiness error', async () => {
    mockStatus.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(STATUS)
    render(<Builder />)

    await waitFor(() => screen.getByText('Could not check coding engines.'))
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(screen.getByText('Build Open Net Worth')).toBeTruthy())
    expect(mockStatus).toHaveBeenCalledTimes(2)
  })
})
