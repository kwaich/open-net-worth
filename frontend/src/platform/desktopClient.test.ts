// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import type {
  DesktopBridge,
  DesktopClientBridge,
  DesktopCommand,
} from '../../../desktop/src/contracts'
import { desktopBridge, desktopClientBridge, subscribeDesktopCommands } from './desktop'

function client(): DesktopClientBridge {
  return {
    version: 1,
    getConnection: vi.fn(async () => ({
      mode: 'server' as const,
      serverUrl: 'https://book.example.test/',
      bookFolder: null,
    })),
    changeConnection: vi.fn(async () => {}),
    openBrowser: vi.fn(async () => {}),
    onCommand: vi.fn(() => () => {}),
  }
}

afterEach(() => {
  delete window.onwDesktop
  delete window.onwDesktopClient
})

it('exposes only the restricted native client in server mode and nothing in an ordinary browser', () => {
  expect(desktopClientBridge()).toBeUndefined()
  const bridge = client()
  window.onwDesktopClient = bridge
  expect(desktopClientBridge()).toBe(bridge)
  expect(desktopBridge()).toBeUndefined()
  expect('getSettings' in desktopClientBridge()!).toBe(false)
  window.onwDesktopClient = { ...bridge, version: 2 } as unknown as DesktopClientBridge
  expect(desktopClientBridge()).toBeUndefined()
})

it('forwards safe client menu commands, ignores local file payloads and unsubscribes', () => {
  const bridge = client()
  const dispose = vi.fn()
  let deliver!: (command: DesktopCommand) => void
  bridge.onCommand = vi.fn((listener) => {
    deliver = listener
    return dispose
  })
  window.onwDesktopClient = bridge
  const listener = vi.fn()
  const unsubscribe = subscribeDesktopCommands(listener)
  deliver({ type: 'open-settings' })
  deliver({ type: 'search' })
  deliver({ type: 'resume' })
  deliver({ type: 'import-files', files: [] })
  expect(listener.mock.calls).toEqual([
    [{ type: 'open-settings' }],
    [{ type: 'search' }],
    [{ type: 'resume' }],
  ])
  unsubscribe()
  expect(dispose).toHaveBeenCalledOnce()
})

it('uses a single full-bridge listener when both native bridges exist', () => {
  const restricted = client()
  const dispose = vi.fn()
  let deliver!: (command: DesktopCommand) => void
  window.onwDesktop = {
    version: 1,
    onCommand: vi.fn((listener) => {
      deliver = listener
      return dispose
    }),
  } as unknown as DesktopBridge
  window.onwDesktopClient = restricted
  const listener = vi.fn()
  const unsubscribe = subscribeDesktopCommands(listener)
  deliver({ type: 'import-files', files: [] })
  expect(listener).toHaveBeenCalledWith({ type: 'import-files', files: [] })
  expect(restricted.onCommand).not.toHaveBeenCalled()
  expect(window.onwDesktop.onCommand).toHaveBeenCalledOnce()
  unsubscribe()
  expect(dispose).toHaveBeenCalledOnce()
})

it('does not register a native command listener in an ordinary browser', () => {
  const listener = vi.fn()
  const unsubscribe = subscribeDesktopCommands(listener)
  unsubscribe()
  expect(listener).not.toHaveBeenCalled()
})
