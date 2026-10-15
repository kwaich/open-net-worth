// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { DesktopBridge } from '../../../../desktop/src/contracts'
import { StepWelcome } from './StepWelcome'

const chooseBookFolder = vi.fn<DesktopBridge['chooseBookFolder']>()
const onLocationChanged = vi.fn()
const props = {
  currency: 'SGD',
  onCurrencyChange: vi.fn(),
  dataDir: '/synthetic/book',
  dataSource: 'env',
  onLocationChanged,
  onNext: vi.fn(),
}

beforeEach(() => {
  chooseBookFolder.mockReset().mockResolvedValue({ cancelled: true, opened: false })
  onLocationChanged.mockReset()
  const settings = {
    bookFolder: props.dataDir,
    browserUrl: null,
    background: false,
    launchAtLogin: false,
    notifications: false,
    watchedFolders: [],
  }
  window.onwDesktop = {
    version: 1,
    chooseBookFolder,
    chooseImportFiles: async () => ({ cancelled: true, files: [] }),
    saveExport: async () => ({ cancelled: true }),
    getSettings: async () => settings,
    updateSettings: async () => settings,
    chooseWatchedFolder: async () => settings,
    revokeWatchedFolder: async () => settings,
    onCommand: () => () => {},
    openExternal: async () => {},
    openBrowser: async () => {},
    retryStartup: async () => {},
  }
})
afterEach(() => {
  cleanup()
  delete window.onwDesktop
})

it('focuses native setup on currency without repeating the chosen folder', () => {
  render(<StepWelcome {...props} />)
  expect(screen.queryByText('ONW_ROOT')).toBeNull()
  expect(screen.queryByText('Working folder')).toBeNull()
  expect(screen.queryByText(/Stored on your machine/)).toBeNull()
  expect(screen.getByText('App currency')).toBeTruthy()
  expect(screen.queryByText('Book folder')).toBeNull()
  expect(screen.queryByText('/synthetic/book')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Change book folder' })).toBeNull()
  expect(chooseBookFolder).not.toHaveBeenCalled()
})

it('keeps the existing server instructions in a browser', () => {
  delete window.onwDesktop
  render(<StepWelcome {...props} />)
  expect(screen.queryByRole('button', { name: 'Change book folder' })).toBeNull()
  expect(screen.getByText('ONW_ROOT')).toBeTruthy()
})
