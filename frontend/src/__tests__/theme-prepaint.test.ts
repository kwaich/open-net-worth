// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { runInNewContext } from 'node:vm'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

// The bootstrap is an external file: the page's CSP allows no inline script.
const page = new DOMParser().parseFromString(
  readFileSync(join(process.cwd(), 'index.html'), 'utf8'),
  'text/html',
)
if (page.querySelector('script:not([src])')) throw new Error('index.html carries an inline script')
const src = page.querySelector('head > script[src$="theme-stamp.js"]')?.getAttribute('src')
if (!src) throw new Error('The pre-paint theme bootstrap is missing')
const bootstrap = readFileSync(join(process.cwd(), 'public', src), 'utf8')

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  document.head.innerHTML = '<meta name="theme-color" content="#0e1014">'
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function runBootstrap() {
  runInNewContext(
    bootstrap!,
    { document, localStorage, window, matchMedia: window.matchMedia },
    {
      timeout: 1000,
    },
  )
}

it.each([
  { stored: null, osLight: true, theme: 'light', color: '#ffffff' },
  { stored: null, osLight: false, theme: 'dark', color: '#0e1014' },
  { stored: 'neon', osLight: true, theme: 'light', color: '#ffffff' },
  { stored: 'neon', osLight: false, theme: 'dark', color: '#0e1014' },
  { stored: 'constructor', osLight: true, theme: 'light', color: '#ffffff' },
  { stored: '__proto__', osLight: true, theme: 'light', color: '#ffffff' },
  { stored: 'system', osLight: true, theme: 'light', color: '#ffffff' },
  { stored: 'system', osLight: false, theme: 'dark', color: '#0e1014' },
  { stored: 'dark', osLight: true, theme: 'dark', color: '#0e1014' },
  { stored: 'light', osLight: false, theme: 'light', color: '#ffffff' },
  { stored: 'warm', osLight: false, theme: 'warm', color: '#edeae3' },
])(
  'paints $theme before React for saved=$stored and OS light=$osLight',
  ({ stored, osLight, theme, color }) => {
    if (stored !== null) localStorage.setItem('onw-theme', stored)
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: osLight }))
    runBootstrap()
    expect(document.documentElement.getAttribute('data-theme')).toBe(theme)
    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe(color)
  },
)

it('follows the OS before React when browser storage is unavailable', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('Storage unavailable')
  })
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: true }))
  runBootstrap()
  expect(document.documentElement.getAttribute('data-theme')).toBe('light')
})

it('uses dark before React when system color-scheme detection is unavailable', () => {
  vi.stubGlobal('matchMedia', undefined)
  runBootstrap()
  expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
})
