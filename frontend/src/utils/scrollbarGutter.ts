// The Lucius transcript (.lx-side-scroll) and its floating composer (.lx-side-foot) declare
// `scrollbar-gutter: stable both-edges` so the message column never shifts when the
// transcript starts overflowing. How much that reserves is the OS's call, not ours:
// ~10-15px per side under classic always-on scrollbars (Windows, Linux, macOS "Always" or a
// mouse plugged in), exactly 0 under macOS overlay scrollbars (the trackpad default), and
// 0 again on phones where index.css hides inner scrollbars. The CSS pads
// `calc(16px - var(--lx-gutter))`, so the VISIBLE inset is 16px in every one of those modes
// instead of assuming a single one — the assumption that shipped a 5px inset to every
// trackpad Mac. These helpers turn the element's real geometry into that variable.

const GUTTER_VAR = '--lx-gutter'

/** Per-side track width the box currently reserves — half of the total, since
 *  `both-edges` mirrors the scrollbar's track on the opposite side too. */
export function scrollbarGutterPx(el: HTMLElement): number {
  return Math.max(0, Math.round((el.offsetWidth - el.clientWidth) / 2))
}

/** Measures `measured` and writes the result to `target` (the panel root, so both the
 *  transcript and the composer inherit one value). Skips the write when nothing changed:
 *  the caller runs this from a ResizeObserver, and a padding change re-fires the observer. */
export function applyScrollbarGutter(measured: HTMLElement, target: HTMLElement): void {
  const next = `${scrollbarGutterPx(measured)}px`
  if (target.style.getPropertyValue(GUTTER_VAR) === next) return
  target.style.setProperty(GUTTER_VAR, next)
}
