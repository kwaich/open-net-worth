import type { ImgHTMLAttributes } from 'react'

type Src = ImgHTMLAttributes<HTMLImageElement>['src']

/** The URL's host when it points off this origin, null when it is safe to render in place. */
export function remoteImageHost(src: Src): string | null {
  if (typeof src !== 'string' || !src) return null
  let url: URL
  try {
    url = new URL(src, window.location.href)
  } catch {
    return src
  }
  if (url.protocol === 'blob:' || url.protocol === 'data:') return null
  if (url.origin === window.location.origin) return null
  return url.host || url.protocol
}
