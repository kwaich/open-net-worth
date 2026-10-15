// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type {
  DesktopBridge,
  DesktopSettings,
  WatchedFolder,
} from '../../../../../../desktop/src/contracts'
import { clearQueryCache, invalidateQueryCache } from '../../../../state/queryCache'
import { DesktopSection } from './DesktopSection'

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../../../../hooks/serverState', () => ({ fetchJson: async () => ({ jobs: [] }) }))

const folder: WatchedFolder = {
  id: 'folder',
  label: 'Statements',
  location: '/synthetic/Statements',
  enabled: true,
  available: true,
}
const original: DesktopSettings = {
  bookFolder: '/synthetic/book',
  browserUrl: 'http://127.0.0.1:8097/',
  background: false,
  notifications: false,
  launchAtLogin: false,
  watchedFolders: [folder],
}
const getSettings = vi.fn<() => Promise<DesktopSettings>>()
const updateSettings = vi.fn<DesktopBridge['updateSettings']>()
const chooseBookFolder = vi.fn<DesktopBridge['chooseBookFolder']>()
const openBrowser = vi.fn<() => Promise<void>>()

beforeEach(() => {
  clearQueryCache()
  delete window.onwDesktopClient
  getSettings.mockReset().mockResolvedValue(original)
  updateSettings.mockReset().mockRejectedValue(new Error('OS refused login registration'))
  chooseBookFolder.mockReset().mockResolvedValue({ cancelled: true, opened: false })
  openBrowser.mockReset().mockResolvedValue(undefined)
  window.onwDesktop = {
    version: 1,
    getSettings,
    updateSettings,
    chooseImportFiles: async () => ({ cancelled: true, files: [] }),
    saveExport: async () => ({ cancelled: true }),
    chooseWatchedFolder: async () => original,
    chooseBookFolder,
    revokeWatchedFolder: async () => original,
    onCommand: () => () => {},
    openExternal: async () => {},
    openBrowser,
    retryStartup: async () => {},
  }
})

it('shows the owned browser address and opens it without caller-supplied arguments', async () => {
  render(<DesktopSection />)
  await screen.findByText(original.browserUrl!)
  fireEvent.click(screen.getByRole('button', { name: 'Open in browser' }))
  await waitFor(() => expect(openBrowser).toHaveBeenCalledWith())
  expect(screen.getByText(/Keep the desktop app running/)).toBeTruthy()
})

it('shows a browser-opening failure and lets the user retry', async () => {
  openBrowser.mockRejectedValueOnce(new Error('Browser refused'))
  render(<DesktopSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Open in browser' }))
  await screen.findByText('Could not open your browser. Try again.')
  fireEvent.click(screen.getByRole('button', { name: 'Open in browser' }))
  await waitFor(() => expect(openBrowser).toHaveBeenCalledTimes(2))
  await waitFor(() =>
    expect(screen.queryByText('Could not open your browser. Try again.')).toBeNull(),
  )
})

it('disables browser opening while the backend is unavailable', async () => {
  getSettings.mockResolvedValue({ ...original, browserUrl: null })
  render(<DesktopSection />)
  const button = await screen.findByRole('button', { name: 'Open in browser' })
  expect((button as HTMLButtonElement).disabled).toBe(true)
  expect(screen.getByText('Browser access is unavailable while the book opens.')).toBeTruthy()
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  delete window.onwDesktop
  delete window.onwDesktopClient
  vi.useRealTimers()
})

it('shows the local Connection panel first and keeps the local desktop workflows', async () => {
  const changeConnection = vi.fn(async () => {})
  window.onwDesktopClient = {
    version: 1,
    getConnection: async () => ({
      mode: 'local',
      serverUrl: null,
      bookFolder: original.bookFolder,
    }),
    changeConnection,
    openBrowser,
    onCommand: () => () => {},
  }
  render(<DesktopSection />)
  await screen.findByText('This computer')
  const connection = screen.getByRole('heading', { name: 'Connection' })
  const background = await screen.findByRole('heading', { name: 'Background activity' })
  expect(
    connection.compareDocumentPosition(background) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy()
  expect(screen.getByRole('heading', { name: 'Statement folders' })).toBeTruthy()
  expect(screen.getByText(original.bookFolder)).toBeTruthy()
  expect(screen.queryByText(/Your book and scheduled work run on this server/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Change connection' }))
  await waitFor(() => expect(changeConnection).toHaveBeenCalledWith())
})

it('refreshes directory availability while Settings stays open', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  render(<DesktopSection />)
  await screen.findByText('Watching')
  getSettings.mockResolvedValue({
    ...original,
    watchedFolders: [{ ...folder, available: false }],
  })
  await act(async () => {
    vi.advanceTimersByTime(5000)
  })
  await screen.findByText('Unavailable')
  expect(screen.queryByText('Watching')).toBeNull()
})

it('revalidates native settings when the desktop resume invalidates screen data', async () => {
  render(<DesktopSection />)
  await screen.findByText('Watching')
  getSettings.mockResolvedValue({
    ...original,
    watchedFolders: [{ ...folder, enabled: false }],
  })
  act(() => invalidateQueryCache(() => true))
  await screen.findByText('Access revoked')
})

it('preserves the confirmed preference and Retry after a rejected save', async () => {
  render(<DesktopSection />)
  const toggle = await screen.findByRole('switch', { name: 'Start at login' })
  fireEvent.click(toggle)
  await screen.findByText('Could not save this desktop setting. Try again.')
  expect(toggle.getAttribute('aria-checked')).toBe('false')
  fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
  await waitFor(() =>
    expect(screen.queryByText('Could not save this desktop setting. Try again.')).toBeNull(),
  )
  expect(toggle.getAttribute('aria-checked')).toBe('false')
})

it('opens the native book picker and preserves the current path on cancellation', async () => {
  render(<DesktopSection />)
  await screen.findByText('/synthetic/book')
  fireEvent.click(screen.getByRole('button', { name: 'Open another folder…' }))
  await waitFor(() => expect(chooseBookFolder).toHaveBeenCalledOnce())
  expect(screen.getByText('/synthetic/book')).toBeTruthy()
  expect(screen.queryByText('Could not open this book folder. Try again.')).toBeNull()
})

it('keeps the previous book visible after a failed picker operation', async () => {
  chooseBookFolder.mockRejectedValueOnce(new Error('Candidate refused'))
  render(<DesktopSection />)
  fireEvent.click(await screen.findByRole('button', { name: 'Open another folder…' }))
  await screen.findByText('Could not open this book folder. Try again.')
  expect(screen.getByText('/synthetic/book')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Open another folder…' }))
  await waitFor(() => expect(chooseBookFolder).toHaveBeenCalledTimes(2))
  await waitFor(() =>
    expect(screen.queryByText('Could not open this book folder. Try again.')).toBeNull(),
  )
})
