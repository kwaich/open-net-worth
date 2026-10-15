// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type {
  DesktopClientBridge,
  DesktopConnection,
} from '../../../../../../desktop/src/contracts'
import { clearQueryCache } from '../../../../state/queryCache'
import { fetchJson } from '../../../../hooks/serverState'
import { DesktopSection } from './DesktopSection'

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../../../../hooks/serverState', () => ({ fetchJson: vi.fn(async () => ({ jobs: [] })) }))

const connection: DesktopConnection = {
  mode: 'server',
  serverUrl: 'https://book.example.test/',
  bookFolder: null,
}
const getConnection = vi.fn<DesktopClientBridge['getConnection']>()
const changeConnection = vi.fn<DesktopClientBridge['changeConnection']>()
const openBrowser = vi.fn<DesktopClientBridge['openBrowser']>()

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  getConnection.mockReset().mockResolvedValue(connection)
  changeConnection.mockReset().mockResolvedValue(undefined)
  openBrowser.mockReset().mockResolvedValue(undefined)
  delete window.onwDesktop
  window.onwDesktopClient = {
    version: 1,
    getConnection,
    changeConnection,
    openBrowser,
    onCommand: () => () => {},
  }
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  delete window.onwDesktop
  delete window.onwDesktopClient
})

it('shows the server connection and work location without local desktop features or polling', async () => {
  render(<DesktopSection />)
  await screen.findByText(connection.serverUrl!)
  expect(screen.getByRole('heading', { name: 'Connection' })).toBeTruthy()
  expect(screen.getByText('Connected to server')).toBeTruthy()
  expect(screen.getByText(/Your book and scheduled work run on this server/)).toBeTruthy()
  expect(screen.queryByText('Background activity')).toBeNull()
  expect(screen.queryByRole('heading', { name: 'Background and startup' })).toBeNull()
  expect(screen.queryByText('Statement folders')).toBeNull()
  expect(screen.queryByText('Your book')).toBeNull()
  expect(screen.queryByRole('switch')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Open another folder…' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Export backup…' })).toBeNull()
  expect(fetchJson).not.toHaveBeenCalled()
})

it('opens the owned connection picker and browser with no caller-supplied URL or folder', async () => {
  render(<DesktopSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Change connection' }))
  await waitFor(() => expect(changeConnection).toHaveBeenCalledWith())
  fireEvent.click(screen.getByRole('button', { name: 'Open in browser' }))
  await waitFor(() => expect(openBrowser).toHaveBeenCalledWith())
  expect(getConnection).toHaveBeenCalledWith()
})

it('disables connection actions while a native request is pending', async () => {
  let complete!: () => void
  changeConnection.mockReturnValueOnce(
    new Promise<void>((resolve) => {
      complete = resolve
    }),
  )
  render(<DesktopSection />)
  const change = await screen.findByRole('button', { name: 'Change connection' })
  fireEvent.click(change)
  expect((change as HTMLButtonElement).disabled).toBe(true)
  expect(
    (screen.getByRole('button', { name: 'Open in browser' }) as HTMLButtonElement).disabled,
  ).toBe(true)
  await act(async () => complete())
  expect((change as HTMLButtonElement).disabled).toBe(false)
})

it('keeps the confirmed server visible after a failed connection change and allows another attempt', async () => {
  changeConnection.mockRejectedValueOnce(new Error('Native picker refused'))
  render(<DesktopSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Change connection' }))
  await screen.findByText('Could not change the connection. Try again.')
  expect(screen.getByText(connection.serverUrl!)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Change connection' }))
  await waitFor(() => expect(changeConnection).toHaveBeenCalledTimes(2))
  await waitFor(() =>
    expect(screen.queryByText('Could not change the connection. Try again.')).toBeNull(),
  )
})

it('shows browser-opening failures without losing the server connection', async () => {
  openBrowser.mockRejectedValueOnce(new Error('Browser refused'))
  render(<DesktopSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Open in browser' }))
  await screen.findByText('Could not open your browser. Try again.')
  expect(screen.getByText(connection.serverUrl!)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Open in browser' }))
  await waitFor(() => expect(openBrowser).toHaveBeenCalledTimes(2))
  await waitFor(() =>
    expect(screen.queryByText('Could not open your browser. Try again.')).toBeNull(),
  )
})

it('offers Retry when native connection metadata cannot be read', async () => {
  getConnection.mockRejectedValueOnce(new Error('IPC unavailable'))
  render(<DesktopSection />)
  await screen.findByText('Could not load the connection. Try again.')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await screen.findByText(connection.serverUrl!)
  expect(screen.queryByText('Could not load the connection. Try again.')).toBeNull()
})

it('renders no desktop controls for an ordinary browser', () => {
  delete window.onwDesktopClient
  const { container } = render(<DesktopSection />)
  expect(container.childElementCount).toBe(0)
  expect(getConnection).not.toHaveBeenCalled()
  expect(fetchJson).not.toHaveBeenCalled()
})

it('opens server background and startup choices through the native dialog without direct writes', async () => {
  const openActivitySettings = vi.fn(async () => {})
  window.onwDesktopClient!.openActivitySettings = openActivitySettings
  render(<DesktopSection />)
  const button = await screen.findByRole('button', { name: 'Background and startup…' })
  expect(screen.getByRole('heading', { name: 'Background and startup' })).toBeTruthy()
  expect(screen.queryByRole('switch')).toBeNull()
  fireEvent.click(button)
  await waitFor(() => expect(openActivitySettings).toHaveBeenCalledWith())
  expect(fetchJson).not.toHaveBeenCalled()
})

it('shows background and startup dialog failures and allows the same button to retry', async () => {
  const openActivitySettings = vi.fn(async () => {})
  openActivitySettings.mockRejectedValueOnce(new Error('Native dialog unavailable'))
  window.onwDesktopClient!.openActivitySettings = openActivitySettings
  render(<DesktopSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Background and startup…' }))
  await screen.findByText('Could not open background and startup settings. Try again.')
  fireEvent.click(screen.getByRole('button', { name: 'Background and startup…' }))
  await waitFor(() => expect(openActivitySettings).toHaveBeenCalledTimes(2))
  await waitFor(() =>
    expect(
      screen.queryByText('Could not open background and startup settings. Try again.'),
    ).toBeNull(),
  )
})

it('disables the background and startup button until the native dialog request completes', async () => {
  let complete!: () => void
  window.onwDesktopClient!.openActivitySettings = () =>
    new Promise<void>((resolve) => {
      complete = resolve
    })
  render(<DesktopSection />)
  const button = await screen.findByRole('button', { name: 'Background and startup…' })
  fireEvent.click(button)
  expect((button as HTMLButtonElement).disabled).toBe(true)
  await act(async () => complete())
  expect((button as HTMLButtonElement).disabled).toBe(false)
})
