// @vitest-environment jsdom
//
// Each FX row can be read either way round: clicking the pair flips it and the
// choice is saved in the widget's own config. VND against a strong base reads
// 0.00004944 one way and ~20,226 the other — only the second is legible.

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))
vi.mock('../../../../state/portfolio', () => ({
  usePortfolioContext: () => ({ rows: [], loading: false }),
}))

import { investingWidgetComponents } from './investing'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'
import { getBaseCurrency } from '../../../../utils/format'

const VND_RATE = 0.00004944 // base units per 1 VND

function jsonOk(body: unknown) {
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
}

function serve(exposure = 2472) {
  authFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/portfolio/analytics')) {
      return jsonOk({ fx_exposure: [{ currency: 'VND', value: exposure }] })
    }
    if (url.startsWith('/api/net-worth')) return jsonOk({ net_worth: 2472 })
    if (url.startsWith('/api/prices')) return jsonOk({ fx: { VND: VND_RATE } })
    return jsonOk({})
  })
}

function renderFx(config: Record<string, unknown> = {}) {
  const Comp = investingWidgetComponents.fx!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'fx')!
  const instance: WidgetInstance = {
    instanceId: 'w-fx',
    defId: 'fx',
    x: 0,
    y: 0,
    w: 4,
    h: 112,
    config,
  }
  const onPatch = vi.fn()
  render(<Comp instance={instance} def={def} onPatch={onPatch} />)
  return onPatch
}

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

describe('FX rate rows', () => {
  it('saves a flipped pair under its unflipped key when the pair is clicked', async () => {
    serve()
    const base = getBaseCurrency()
    const onPatch = renderFx({ pairs: [] })
    const pair = await screen.findByRole('button', { name: new RegExp(`^VND / ${base}`) })
    fireEvent.click(pair)
    expect(onPatch).toHaveBeenCalledWith({ config: { pairs: [], flipped: [`VND/${base}`] } })
  })

  it('reads a flipped pair the other way round, at the inverse rate', async () => {
    serve()
    const base = getBaseCurrency()
    renderFx({ flipped: [`VND/${base}`] })
    await screen.findByRole('button', { name: new RegExp(`^${base} / VND`) })
    await waitFor(() => expect(screen.getByText(/VND$/, { selector: '.ov-fx-rate' })).toBeTruthy())
    const shown = screen.getByText(/VND$/, { selector: '.ov-fx-rate' }).textContent ?? ''
    expect(Number(shown.replace(/[^\d.]/g, ''))).toBeCloseTo(1 / VND_RATE, 0)
  })

  it('un-flips a flipped pair on a second click', async () => {
    serve()
    const base = getBaseCurrency()
    const onPatch = renderFx({ flipped: [`VND/${base}`] })
    fireEvent.click(await screen.findByRole('button', { name: new RegExp(`^${base} / VND`) }))
    expect(onPatch).toHaveBeenCalledWith({ config: { flipped: [] } })
  })

  it('says a net-negative currency position is owed, never a negative amount held', async () => {
    serve(-362660)
    renderFx({ pairs: [] })
    await screen.findByText(/owed$/, { selector: '.ov-fx-note' })
    expect(screen.queryByText(/held$/, { selector: '.ov-fx-note' })).toBeNull()
    expect(screen.getByText(/owed$/, { selector: '.ov-fx-note' }).textContent).not.toContain('-')
  })

  it('still says held for a positive position', async () => {
    serve()
    renderFx({ pairs: [] })
    await screen.findByText(/held$/, { selector: '.ov-fx-note' })
  })
})
