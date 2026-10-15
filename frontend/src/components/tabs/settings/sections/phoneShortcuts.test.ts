import { describe, expect, it } from 'vitest'
import { addressWarning, iosMajor, phonePlatform } from './phoneShortcuts'

describe('phonePlatform', () => {
  it.each([
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)', 0, 'ios'],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)', 5, 'ios'], // iPadOS desktop UA
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)', 0, 'desktop'],
    ['Mozilla/5.0 (Linux; Android 15; Pixel 9)', 5, 'android'],
    ['Mozilla/5.0 (Windows NT 10.0; Win64; x64)', 0, 'desktop'],
  ])('%s (%i touch) → %s', (ua, touch, expected) => {
    expect(phonePlatform(ua, touch)).toBe(expected)
  })
})

describe('addressWarning', () => {
  it.each([
    'https://w7dvrf.opennetworth.cloud',
    'http://100.101.12.4:8080', // Tailscale CGNAT range
    'http://box.tail1234.ts.net:8080',
  ])('accepts %s', (url) => {
    expect(addressWarning(url)).toBeNull()
  })

  it.each([
    ['http://localhost:8080', "this computer's own address"],
    ['http://127.0.0.1:8080', "this computer's own address"],
    ['http://192.168.1.20:8080', 'home Wi-Fi'],
    ['http://mybox.local:8080', 'home Wi-Fi'],
    ['http://box.example.com', 'unencrypted'],
    ['box.example.com', 'full address'],
  ])('warns on %s', (url, phrase) => {
    expect(addressWarning(url)).toContain(phrase)
  })
})

describe('iosMajor', () => {
  it.each([
    // Safari froze the OS number at 18_x from iOS 26; its Version/NN is the iOS version.
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) Version/27.0 Mobile Safari/604.1', 27],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) Version/26.1 Mobile Safari/604.1', 26],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Version/17.5 Mobile Safari/604.1', 17],
    ['Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) Mobile/15E148', 18],
    ['Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0)', null],
    ['Mozilla/5.0 (Linux; Android 15; Pixel 9)', null],
  ])('%s → %s', (ua, major) => {
    expect(iosMajor(ua)).toBe(major)
  })
})
