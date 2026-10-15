// Pure helpers for Settings → Phone shortcuts: which phone this is, and whether the address
// the phone will be handed can actually be reached from it.

export type PhonePlatform = 'ios' | 'android' | 'desktop'

/** iPadOS reports a Mac user agent, so a touch-capable "Macintosh" is an iPad. */
export function phonePlatform(
  ua: string = navigator.userAgent,
  touchPoints: number = navigator.maxTouchPoints ?? 0,
): PhonePlatform {
  if (/Android/i.test(ua)) return 'android'
  if (/iPhone|iPad|iPod/i.test(ua) || (/Macintosh/i.test(ua) && touchPoints > 1)) return 'ios'
  return 'desktop'
}

/** The iPhone's iOS major version, or null off an iPhone/unknown. Safari froze the OS number in
 *  its user agent at 18_x from iOS 26, so its ``Version/NN`` (Safari = iOS version) wins. */
export function iosMajor(ua: string = navigator.userAgent): number | null {
  const safari = /Version\/(\d+)/.exec(ua)
  if (safari && Number(safari[1]) >= 26) return Number(safari[1])
  const os = /OS (\d+)_/.exec(ua)
  return os ? Number(os[1]) : null
}

const LOOPBACK = /^(localhost|127\.\d+\.\d+\.\d+|\[::1\]|0\.0\.0\.0)$/i
const PRIVATE_LAN =
  /^(10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|[^.]+\.local|[^.]+)$/i
const TAILNET = /^(100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+|.+\.ts\.net)$/i

/** True when ``url`` names this computer itself — a QR for it can never work from a phone. */
export function isOwnComputer(url: string): boolean {
  try {
    return LOOPBACK.test(new URL(url).hostname)
  } catch {
    return false
  }
}

/** Why the phone can't use ``url`` (or can only partly), or null when it looks reachable. */
export function addressWarning(url: string): string | null {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return 'Enter a full address, like https://…'
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return 'Enter a full address, like https://…'
  }
  const host = parsed.hostname
  if (LOOPBACK.test(host)) {
    return "Your phone can't open this computer's own address. Use your tunnel, Tailscale or HTTPS address."
  }
  if (TAILNET.test(host)) return null
  if (PRIVATE_LAN.test(host)) {
    return 'Only works on your home Wi-Fi, not at the shop.'
  }
  if (parsed.protocol === 'http:') {
    return 'Use https:// — plain http:// sends your token unencrypted.'
  }
  return null
}
