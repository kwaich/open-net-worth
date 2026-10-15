// Scroll-edge fades for Documents' scrolling regions: a DOM attribute (`data-fade`), not
// state — it changes on every scroll event and nothing else re-renders from it. CSS turns it
// into a mask (vault.css), so content fades out at an edge with more to scroll to instead of
// being cut at a hard line.

/** Top/bottom: the list, an item's page. */
export function markFades(el: HTMLElement | null) {
  if (!el) return
  const edges = []
  if (el.scrollTop > 0) edges.push('top')
  if (el.scrollTop + el.clientHeight < el.scrollHeight - 1) edges.push('bottom')
  el.dataset.fade = edges.join(' ')
}

/** Left/right: the filter chip row. */
export function markSideFades(el: HTMLElement | null) {
  if (!el) return
  const edges = []
  if (el.scrollLeft > 1) edges.push('left')
  if (el.scrollLeft + el.clientWidth < el.scrollWidth - 1) edges.push('right')
  el.dataset.fade = edges.join(' ')
}
