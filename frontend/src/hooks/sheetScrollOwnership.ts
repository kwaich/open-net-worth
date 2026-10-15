// sheetScrollOwnership — WHO owns a touch inside a mobile sheet: the browser (a scroll) or the
// sheet (a drag). Pure DOM predicates plus the non-passive touchmove veto, shared by
// useSheetDragToDismiss's drag decision and its veto so the two can never disagree. No React,
// no physics — see the hook's top-of-file note (points 1, 3 and 4) for the contract.

// Spread onto chrome that sits inside a scroll container but must drag like a handle (point 4
// in the hook's note). Pair it with `touch-action: none` on the same element so the browser
// never starts a scroll for a touch that begins there.
const DRAG_SURFACE_ATTR = 'data-sheet-drag-surface'
export const DRAG_SURFACE_PROPS: Record<typeof DRAG_SURFACE_ATTR, ''> = { [DRAG_SURFACE_ATTR]: '' }

// The opposite opt-out: spread onto a region that owns its OWN touch gestures (the fullscreen
// price chart's pane — drag to pan, pinch to zoom). A touch that starts inside it is never the
// sheet's: not a drag, not a dismiss, whatever direction it travels. The sheet's `touch-action:
// none` root still keeps the browser from scrolling/zooming the page for that touch, which is
// exactly what a JS-handled gesture surface wants.
const DRAG_IGNORE_ATTR = 'data-sheet-drag-ignore'
export const DRAG_IGNORE_PROPS: Record<typeof DRAG_IGNORE_ATTR, ''> = { [DRAG_IGNORE_ATTR]: '' }

/** Whether a touch starting at `start` belongs to a gesture-owning region inside `boundary`. */
export function ownsItsGesture(start: Element, boundary: Element): boolean {
  for (let node: Element | null = start; node; node = node.parentElement) {
    if (node.hasAttribute(DRAG_IGNORE_ATTR)) return true
    if (node === boundary) return false
  }
  return false
}

const SCROLLING_OVERFLOW = /(auto|scroll|overlay)/

/** Scrollable on EITHER axis — a wide table that fits vertically still owns a sideways pan. */
function isScrollable(el: Element): boolean {
  const style = getComputedStyle(el)
  return (
    (el.scrollHeight > el.clientHeight && SCROLLING_OVERFLOW.test(style.overflowY)) ||
    (el.scrollWidth > el.clientWidth && SCROLLING_OVERFLOW.test(style.overflowX))
  )
}

/** The scroll container that would absorb a touch starting at `start`, or null when the sheet
 * owns it outright. Walks up to (and including) the sheet root — the root itself may be the
 * scroll container (TxnDetail's sheet is) — and stops short at an opted-in drag surface. */
export function findScrollableAncestor(start: Element, boundary: Element): Element | null {
  for (let node: Element | null = start; node; node = node.parentElement) {
    if (node.hasAttribute(DRAG_SURFACE_ATTR)) return null
    if (isScrollable(node)) return node
    if (node === boundary) return null
  }
  return null
}

function atTop(el: Element): boolean {
  return el.scrollTop <= 0
}

function atBottom(el: Element): boolean {
  return el.scrollTop >= el.scrollHeight - el.clientHeight
}

/** The one rule shared by the touch veto and the drag decision: the sheet owns a movement of
 * `dy` (positive = finger moving down) when nothing under the finger can scroll to absorb it.
 * A `sideways`-dominant gesture over anything scrollable is a horizontal scroll (the categories
 * table pans on both axes), never a pull — the browser keeps it whatever the vertical edge. */
export function sheetOwnsMovement(
  scrollEl: Element | null,
  dy: number,
  sideways: boolean,
): boolean {
  if (!scrollEl) return true
  if (sideways) return false
  return (dy > 0 && atTop(scrollEl)) || (dy < 0 && atBottom(scrollEl))
}

/** Non-passive touch listeners on the sheet root that cancel native scrolling exactly when the
 * sheet is about to own the movement (point 3 in the hook's note), and for EVERY movement of a
 * touch the hook has already claimed as a drag — a finger that reverses mid-drag must
 * rubber-band the sheet, not start scrolling the content underneath. Bound lazily from the
 * first pointerdown rather than in a mount effect: Radix unmounts Modal's card whenever it is
 * closed, so a ref-at-mount would usually be null. Returns the unbind. */
export function bindTouchVeto(root: HTMLElement, dragging: () => boolean): () => void {
  let scrollEl: Element | null = null
  let startX = 0
  let startY = 0
  let lastY = 0
  let tracking = false
  const onTouchStart = (event: TouchEvent) => {
    // A second finger (pinch-zoom, or a stray touch) is never ours to cancel — and stays not
    // ours even after one of them lifts.
    tracking = event.touches.length === 1
    if (!tracking) return
    scrollEl = findScrollableAncestor(event.target as Element, root)
    startX = event.touches[0]!.clientX
    startY = lastY = event.touches[0]!.clientY
  }
  const onTouchMove = (event: TouchEvent) => {
    if (!tracking || event.touches.length !== 1 || !event.cancelable) return
    const { clientX: x, clientY: y } = event.touches[0]!
    const dy = y - lastY
    lastY = y
    // Direction is per-event (a reversal matters); the axis is the gesture's as a whole, so a
    // pixel of vertical jitter on a sideways pan cannot cancel the browser's horizontal scroll.
    const sideways = Math.abs(x - startX) > Math.abs(y - startY)
    if (dragging() || (dy !== 0 && sheetOwnsMovement(scrollEl, dy, sideways))) {
      event.preventDefault()
    }
  }
  root.addEventListener('touchstart', onTouchStart, { passive: true })
  root.addEventListener('touchmove', onTouchMove, { passive: false })
  return () => {
    root.removeEventListener('touchstart', onTouchStart)
    root.removeEventListener('touchmove', onTouchMove)
  }
}
