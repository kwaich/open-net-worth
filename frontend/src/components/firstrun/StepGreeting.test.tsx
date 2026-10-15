// @vitest-environment jsdom
//
// First run, screen 00 — the greeting shown before anything is asked. It owns no state; its
// only job is routing to "Set up" (step 0) or the restore branch, so these tests pin exactly
// that: each action fires its own callback and neither fires the other's.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { StepGreeting } from './StepGreeting'

afterEach(() => {
  cleanup()
})

describe('StepGreeting', () => {
  it('routes "Get started" into ordinary setup', () => {
    const onSetup = vi.fn()
    render(<StepGreeting onSetup={onSetup} />)

    fireEvent.click(screen.getByRole('button', { name: /^get started$/i }))

    expect(onSetup).toHaveBeenCalledTimes(1)
  })

  it('offers only Set up — restoring a backup comes after choosing the folder', () => {
    const onSetup = vi.fn()
    render(<StepGreeting onSetup={onSetup} />)

    expect(screen.queryByRole('button', { name: /backup to restore/i })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^get started$/i }))
    expect(onSetup).toHaveBeenCalledTimes(1)
  })

  it('previews the three setup steps that follow', () => {
    render(<StepGreeting onSetup={() => {}} />)

    expect(screen.getByText(/the currency you think in/i)).toBeTruthy()
    expect(screen.getByText(/bring your own model/i)).toBeTruthy()
    expect(screen.getByText(/a password, if you want one/i)).toBeTruthy()
  })

  it('in gateway mode drops the password item and the machine-specific wording', () => {
    render(<StepGreeting onSetup={() => {}} gatewayMode />)

    expect(screen.getByText(/the currency you think in/i)).toBeTruthy()
    expect(screen.getByText(/bring your own model/i)).toBeTruthy()
    expect(screen.queryByText(/password/i)).toBeNull()
    expect(screen.queryByText(/this machine/i)).toBeNull()
  })

  it('in gateway mode states hosted-accurate copy: no local model, data on your own server', () => {
    render(<StepGreeting onSetup={() => {}} gatewayMode />)

    expect(screen.getByText('Claude Code, Codex, or an API key.')).toBeTruthy()
    expect(screen.queryByText(/ollama/i)).toBeNull()
    expect(screen.getByText('No telemetry. Your data stays on your own server.')).toBeTruthy()
    expect(screen.queryByText(/no upload/i)).toBeNull()
  })
})
