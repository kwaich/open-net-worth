// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../../../state/queryCache'
import { takeSendIntent } from '../../../state/luciusSendIntent'
import { OutlookSetupModal } from './OutlookSetupModal'

const authFetch = vi.fn<typeof fetch>()
vi.mock('../../../api/client', () => ({
  authFetch: (...args: Parameters<typeof fetch>) => authFetch(...args),
}))

const ok = (body: unknown = {}) =>
  Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))

function setup(props: Partial<React.ComponentProps<typeof OutlookSetupModal>> = {}) {
  const onSaved = vi.fn()
  const onAskLucius = vi.fn()
  render(
    <OutlookSetupModal
      suggestedSpending={5200}
      needsBirthDate
      needsSpending
      onSaved={onSaved}
      onAskLucius={onAskLucius}
      {...props}
    />,
  )
  return { onSaved, onAskLucius }
}

beforeEach(() => {
  clearQueryCache()
  authFetch.mockReset()
})
afterEach(cleanup)

describe('OutlookSetupModal', () => {
  it('is a dialog with no way to close it', () => {
    setup()
    const dialog = screen.getByRole('dialog', { name: 'Will your money last?' })
    expect(dialog.getAttribute('aria-modal')).toBeNull()
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /close/i })).toBeNull()
  })

  it('asks for both answers and prefills spending from the profile', () => {
    setup()
    expect(screen.getByText("Two answers and you'll see when you could retire.")).toBeTruthy()
    expect(screen.getByLabelText('Your birth date')).toBeTruthy()
    expect(
      (screen.getByLabelText(/Monthly spending in retirement/) as HTMLInputElement).value,
    ).toBe('5,200')
  })

  it('shows only the birth date when spending is known', () => {
    setup({ needsSpending: false })
    expect(screen.getByLabelText('Your birth date')).toBeTruthy()
    expect(screen.queryByLabelText(/Monthly spending/)).toBeNull()
    expect(screen.getByText("One answer and you'll see when you could retire.")).toBeTruthy()
  })

  it('shows only spending when the birth date is known', () => {
    setup({ needsBirthDate: false, suggestedSpending: 0 })
    expect(screen.queryByLabelText('Your birth date')).toBeNull()
    expect((screen.getByLabelText(/Monthly spending/) as HTMLInputElement).value).toBe('')
  })

  it('disables Show my outlook until the answers are valid', () => {
    setup()
    const show = screen.getByRole('button', { name: 'Show my outlook' }) as HTMLButtonElement
    expect(show.disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Your birth date'), { target: { value: '1988-04-12' } })
    expect(show.disabled).toBe(false)
    fireEvent.change(screen.getByLabelText('Your birth date'), { target: { value: '2999-01-01' } })
    expect(show.disabled).toBe(true)
  })

  it('enables Show my outlook as soon as an amount is typed, before the field blurs', () => {
    setup({ suggestedSpending: 0, needsBirthDate: false })
    const show = screen.getByRole('button', { name: 'Show my outlook' }) as HTMLButtonElement
    expect(show.disabled).toBe(true)
    const field = screen.getByLabelText('Monthly spending in retirement')
    fireEvent.focus(field)
    fireEvent.input(field, { target: { value: '4000' } })
    expect(show.disabled).toBe(false)
    fireEvent.input(field, { target: { value: '0' } })
    expect(show.disabled).toBe(true)
  })

  it('saves the birth date and the spending, then tells the page', async () => {
    authFetch.mockImplementation(() => ok({ profile: { monthly_expenses: 5200 } }))
    const { onSaved } = setup()
    fireEvent.change(screen.getByLabelText('Your birth date'), { target: { value: '1988-04-12' } })
    fireEvent.click(screen.getByRole('button', { name: 'Show my outlook' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    const bodies = authFetch.mock.calls
      .filter(([, init]) => init?.method === 'PUT')
      .map(([, init]) => JSON.parse(String(init?.body)))
    expect(bodies[0]).toEqual({ date_of_birth: '1988-04-12' })
    expect(bodies[1]).toMatchObject({ fire_monthly_retirement_spending: 5200 })
  })

  it('asks what they save now when nothing says, and stores it as a saving, not as income', async () => {
    authFetch.mockImplementation(() => ok({ profile: {} }))
    const { onSaved } = setup({ needsBirthDate: false, needsSaving: true })
    expect(screen.getByText("Two answers and you'll see when you could retire.")).toBeTruthy()
    const show = screen.getByRole('button', { name: 'Show my outlook' }) as HTMLButtonElement
    expect(show.disabled).toBe(true)
    const field = screen.getByLabelText('Monthly saving now')
    fireEvent.focus(field)
    fireEvent.input(field, { target: { value: '0' } })
    expect(show.disabled).toBe(false) // saving nothing yet is a real answer
    fireEvent.change(field, { target: { value: '1500' } })
    fireEvent.blur(field)
    fireEvent.click(show)
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1))
    const bodies = authFetch.mock.calls
      .filter(([, init]) => init?.method === 'PUT')
      .map(([, init]) => JSON.parse(String(init?.body)))
    expect(bodies[0]).toEqual({
      fire_monthly_retirement_spending: 5200,
      monthly_saving: 1500,
    })
  })

  it('stays up and says so when the save fails', async () => {
    authFetch.mockImplementation(() => Promise.resolve(new Response('{}', { status: 500 })))
    const { onSaved } = setup({ needsSpending: false })
    fireEvent.change(screen.getByLabelText('Your birth date'), { target: { value: '1988-04-12' } })
    fireEvent.click(screen.getByRole('button', { name: 'Show my outlook' }))
    await screen.findByRole('alert')
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('hands the setup to Lucius to be sent', () => {
    const { onAskLucius } = setup()
    fireEvent.click(screen.getByRole('button', { name: 'Set up with Lucius' }))
    expect(onAskLucius).toHaveBeenCalledWith(expect.stringContaining('retirement outlook'))
    expect(takeSendIntent()).toBe(true)
    expect(screen.getByRole('dialog')).toBeTruthy()
  })
})
