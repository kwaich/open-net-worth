// Overview v2 — one delegated tooltip for every chart/diagram in the tab.
// Any element carrying a `data-tip` attribute (a bar segment, a Sankey node, a
// treemap tile…) shows a rich cursor-following tooltip through a single
// delegated listener, instead of each chart wiring its own. Ported from the
// handoff's `tip.jsx`. Self-installs once on first import; call
// `installOvTooltip()` from the Overview page mount so it's guaranteed to run.
//
// Touch: a finger has no hover, so a TAP on a `data-tip` element pins the tooltip
// open instead of activating the element. A second tap on the SAME element lets
// the click through (so a clickable tile still opens); a tap anywhere else, a
// scroll, or a blur dismisses it. An element that wants to say so carries
// `data-tip-tap="Tap again to open"` — appended only for touch, never for a mouse.

let installed = false
let el: HTMLDivElement | null = null
let pinned: Element | null = null
let lastPointerWasTouch = false

function ensure(): HTMLDivElement {
  if (!el) {
    el = document.createElement('div')
    el.className = 'ov-htip'
    el.setAttribute('aria-hidden', 'true')
    document.body.appendChild(el)
  }
  return el
}

function hide(): void {
  pinned = null
  if (el) {
    el.style.opacity = '0'
    el.style.transform = 'translateY(2px)'
  }
}

function line(cls: string, text: string): HTMLDivElement {
  const row = document.createElement('div')
  row.className = cls
  row.textContent = text
  return row
}

// The dot colour is data (a category swatch), so it is only ever assigned to a style property
// after the browser itself confirms it parses as a CSS colour — never spliced into markup.
function isCssColor(color: string): boolean {
  if (typeof CSS !== 'undefined' && typeof CSS.supports === 'function') {
    return CSS.supports('color', color)
  }
  return /^#[0-9a-f]{6}$/i.test(color)
}

function dot(color: string | null): HTMLSpanElement | null {
  if (!color || !isCssColor(color)) return null
  const span = document.createElement('span')
  span.className = 'ov-htip-dot'
  span.style.background = color
  return span
}

function show(target: Element, x: number, y: number, touch = false): void {
  const tip = target.getAttribute('data-tip')
  if (!tip) {
    hide()
    return
  }
  const color = target.getAttribute('data-tip-color')
  const tapHint = touch ? target.getAttribute('data-tip-tap') : null
  const box = ensure()
  const [title = '', ...rows] = tip.split('\n')
  const head = line('ov-htip-t', '')
  const swatch = dot(color)
  if (swatch) head.appendChild(swatch)
  const label = document.createElement('span')
  label.textContent = title
  head.appendChild(label)
  box.replaceChildren(head, ...rows.map((r) => line('ov-htip-r', r)))
  if (tapHint) box.appendChild(line('ov-htip-tap', tapHint))
  box.style.opacity = '1'
  box.style.transform = 'none'
  const w = box.offsetWidth
  const h = box.offsetHeight
  const pad = 14
  let nx = x + pad
  let ny = y - h - pad
  if (nx + w > window.innerWidth - 8) nx = x - w - pad
  if (nx < 8) nx = 8
  if (ny < 8) ny = y + pad
  box.style.left = nx + 'px'
  box.style.top = ny + 'px'
}

function tipTarget(e: Event): Element | null {
  return (e.target as Element | null)?.closest?.('[data-tip]') ?? null
}

/** Capture-phase, so it runs before React's root listener: a first tap on a tipped element
 *  is consumed here and never reaches the element's own onClick. ANY other click — a second
 *  tap, a tap elsewhere, or a mouse click after the user switches from touch to a trackpad —
 *  unpins, so a stale pin can never leave hover tooltips dead for the session. */
function onClickCapture(e: MouseEvent): void {
  const t = tipTarget(e)
  if (lastPointerWasTouch && t && t !== pinned) {
    show(t, e.clientX, e.clientY, true)
    pinned = t
    e.stopPropagation()
    e.preventDefault()
    return
  }
  hide()
}

export function installOvTooltip(): void {
  if (installed) return
  installed = true
  document.addEventListener(
    'pointerdown',
    (e) => {
      lastPointerWasTouch = e.pointerType === 'touch' || e.pointerType === 'pen'
    },
    true,
  )
  document.addEventListener(
    'mousemove',
    (e) => {
      if (pinned) return
      const t = tipTarget(e)
      if (t) show(t, e.clientX, e.clientY)
      else hide()
    },
    true,
  )
  document.addEventListener('click', onClickCapture, true)
  document.addEventListener(
    'mouseleave',
    () => {
      if (!pinned) hide()
    },
    true,
  )
  window.addEventListener('scroll', hide, true)
  window.addEventListener('blur', hide)
}

/** Test seam — dismiss any pinned tooltip and forget the last pointer type. */
export function _resetOvTooltipForTests(): void {
  hide()
  lastPointerWasTouch = false
}
