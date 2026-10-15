// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

let mod: typeof import('./gatewayReauth')

beforeEach(async () => {
  sessionStorage.clear()
  vi.resetModules()
  mod = await import('./gatewayReauth')
})
afterEach(() => sessionStorage.clear())

const jsonRes = (body: unknown, type = 'application/json') =>
  new Response(JSON.stringify(body), { status: 401, headers: { 'content-type': type } })

describe('reauthUrlFrom', () => {
  it('reads the reauth url of a gateway 401', async () => {
    expect(await mod.reauthUrlFrom(jsonRes({ reauth: 'https://cloud.example/connect?x=1' }))).toBe(
      'https://cloud.example/connect?x=1',
    )
  })

  it.each([
    [{ reauth: 'javascript:alert(1)' }],
    [{ reauth: '/relative' }],
    [{ reauth: 42 }],
    [{ detail: 'authentication required' }],
  ])('ignores %j', async (body) => {
    expect(await mod.reauthUrlFrom(jsonRes(body))).toBeUndefined()
  })

  it('ignores a non-JSON body', async () => {
    expect(await mod.reauthUrlFrom(jsonRes('x', 'text/html'))).toBeUndefined()
  })
})

describe('startGatewayReauth', () => {
  it('goes to the reauth url and announces it', () => {
    mod.setGatewayPortal('https://cloud.example')
    const go = vi.fn()
    const seen = vi.fn()
    mod.subscribeReauthing(seen)
    expect(mod.startGatewayReauth('https://cloud.example/r', go, 1_000_000)).toBe(true)
    expect(go).toHaveBeenCalledWith('https://cloud.example/r')
    expect(mod.isReauthing()).toBe(true)
    expect(seen).toHaveBeenCalled()
  })

  it('saves registered drafts, latest source first, and hands them back once after the trip', async () => {
    mod.registerDraftSource('composer', () => ({ text: 'old copy' }))
    mod.registerDraftSource('composer', () => ({ text: 'live text' }))
    mod.registerDraftSource('empty', () => null)
    mod.startGatewayReauth('https://cloud.example/r', vi.fn(), Date.now())

    vi.resetModules() // the page reloads: a fresh module on return
    const back = await import('./gatewayReauth')
    expect(back.takeReauthDraft('composer')).toEqual({ text: 'live text' })
    expect(back.takeReauthDraft('composer')).toBeUndefined()
    expect(back.takeReauthDraft('empty')).toBeUndefined()
  })

  it('drops drafts older than the window', async () => {
    mod.registerDraftSource('composer', () => 'x')
    mod.startGatewayReauth('https://cloud.example/r', vi.fn(), Date.now() - 60 * 60_000)
    vi.resetModules()
    expect((await import('./gatewayReauth')).takeReauthDraft('composer')).toBeUndefined()
  })

  it('a deliberate click always goes, even right after another sign-in', () => {
    mod.setGatewayPortal('https://cloud.example')
    const now = 5_000_000
    expect(mod.startGatewayReauth('https://cloud.example/r', vi.fn(), now)).toBe(true)
    const go = vi.fn()
    expect(mod.startGatewayReauth('https://cloud.example/r', go, now + 1_000)).toBe(true) // already reauthing
    expect(go).not.toHaveBeenCalled()
    expect(mod.startGatewayReauth('https://cloud.example/r', go, now + 1_000, true)).toBe(true)
    expect(go).toHaveBeenCalledWith('https://cloud.example/r')
  })

  it('does not loop: a second attempt inside the interval is refused', () => {
    const go = vi.fn()
    mod.startGatewayReauth('https://cloud.example/r', go, 1_000_000)
    return import('./gatewayReauth').then(async () => {
      vi.resetModules()
      const fresh = await import('./gatewayReauth')
      expect(
        fresh.startGatewayReauth(
          'https://cloud.example/r',
          go,
          1_000_000 + fresh.REAUTH_MIN_INTERVAL_MS - 1,
        ),
      ).toBe(false)
      expect(go).toHaveBeenCalledTimes(1)
    })
  })

  it('a source that throws does not stop the sign-in', () => {
    mod.registerDraftSource('bad', () => {
      throw new Error('boom')
    })
    const go = vi.fn()
    expect(mod.startGatewayReauth('https://cloud.example/r', go, 1)).toBe(true)
    expect(go).toHaveBeenCalled()
  })
})

describe('freshSignInUrl', () => {
  it('goes through the gateway refresh and comes back to the same page', () => {
    window.history.replaceState(null, '', '/settings/terminal?x=1')
    const url = new URL(mod.freshSignInUrl())
    expect(url.origin).toBe(window.location.origin)
    expect(url.pathname).toBe('/_gateway/refresh')
    expect(url.searchParams.get('return')).toBe('/settings/terminal?x=1')
  })
})

describe('desktopFreshSignIn', () => {
  afterEach(() => {
    delete window.onwDesktopClient
  })

  it('is null in a browser, where the gateway refresh applies', () => {
    expect(mod.desktopFreshSignIn()).toBeNull()
    window.onwDesktopClient = { version: 1 } as never // an older app without the call
    expect(mod.desktopFreshSignIn()).toBeNull()
  })

  it('hands the desktop app the page to come back to', async () => {
    window.history.replaceState(null, '', '/settings/terminal?x=1')
    const requestFreshSignIn = vi.fn().mockResolvedValue(undefined)
    window.onwDesktopClient = { version: 1, requestFreshSignIn } as never
    await mod.desktopFreshSignIn()
    expect(requestFreshSignIn).toHaveBeenCalledWith('/settings/terminal?x=1')
  })
})

describe('which reauth URL is followed', () => {
  it('follows the portal and this site, and reloads for anything else', () => {
    mod.setGatewayPortal('https://cloud.example/some/path')
    const go = vi.fn()
    mod.startGatewayReauth('https://cloud.example/connect?x=1', go, 1)
    expect(go).toHaveBeenLastCalledWith('https://cloud.example/connect?x=1')

    sessionStorage.clear()
    vi.resetModules()
    return import('./gatewayReauth').then((fresh) => {
      fresh.setGatewayPortal('https://cloud.example')
      const elsewhere = vi.fn()
      fresh.startGatewayReauth('https://evil.example/phish', elsewhere, 1)
      expect(elsewhere).toHaveBeenLastCalledWith(window.location.href)
    })
  })

  it('without a known portal only this site qualifies', () => {
    const go = vi.fn()
    mod.startGatewayReauth('https://cloud.example/connect', go, 1)
    expect(go).toHaveBeenLastCalledWith(window.location.href)
    sessionStorage.clear()
  })
})
