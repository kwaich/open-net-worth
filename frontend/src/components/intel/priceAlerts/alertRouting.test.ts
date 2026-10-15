import { describe, expect, it } from 'vitest'
import { channelKey, notifyOptions, resolveRoute, routeHelp, routeSummary } from './alertRouting'
import type { AlertRouting } from './alertRouting'

const tg = { id: 'telegram', label: 'Telegram', available: true }
const email = { id: 'email', label: 'Email', available: true }
const hook = { id: 'webhook', label: 'Webhook', available: false }
const r: AlertRouting = { channels: [email, hook, tg], defaults: ['telegram'] }

describe('alert routing', () => {
  it('offers every combination of the channels that are set up, email-only included', () => {
    expect(notifyOptions(r, ['telegram']).map((o) => o.label)).toEqual([
      'In-app only',
      'In-app + Email',
      'In-app + Telegram',
      'In-app + Email + Telegram',
    ])
  })

  it('points at Connectors only for a channel you can actually add there', () => {
    const noTelegram: AlertRouting = { ...r, channels: [email, hook, { ...tg, available: false }] }
    const last = notifyOptions(noTelegram, []).at(-1)
    expect(last).toMatchObject({ label: 'Telegram: set up in Connectors', disabled: true })
    // Webhook has no Connectors card, so it is never offered as something to set up.
    expect(notifyOptions(noTelegram, []).some((o) => o.label.includes('Webhook'))).toBe(false)
  })

  it('keeps a saved channel that is now off selectable, and flags it', () => {
    const opts = notifyOptions(r, ['telegram', 'webhook'])
    const saved = opts.find((o) => o.value === channelKey(['webhook', 'telegram'], r))
    expect(saved).toMatchObject({ label: 'In-app + Telegram + Webhook (off)' })
    expect(saved?.disabled).toBeFalsy()
  })

  it('gives one set one key, whatever order it was saved in', () => {
    expect(channelKey(['telegram', 'email'], r)).toBe(channelKey(['email', 'telegram'], r))
  })

  it('splits a saved channel that is off or removed from the live ones', () => {
    expect(resolveRoute(['telegram', 'webhook', 'slack'], r)).toEqual({
      live: ['Telegram'],
      off: ['Webhook', 'Slack'],
    })
    expect(routeHelp(['telegram', 'webhook'], r)).toBe(
      "Shows in Intel and on Telegram. Webhook is off in Connectors, so it's skipped.",
    )
  })

  it('says plainly when nothing outside the app will be reached', () => {
    expect(routeHelp([], r)).toBe('Shows in Intel. Nothing is sent outside the app.')
    expect(routeHelp(['webhook'], r)).toMatch(/^Only shows in Intel for now\./)
    expect(routeSummary(['webhook'], r)).toBe('In-app only · channels off')
    expect(routeSummary(['email'], r)).toBe('In-app + Email')
  })
})
