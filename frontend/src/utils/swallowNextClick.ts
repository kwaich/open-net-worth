// A phone sheet closes from its dimmed backdrop on the way DOWN — iPhone Safari may never send
// the backdrop a click at all. But once the finger lifts, Safari delivers that tap's click to
// whatever is under it by then: the backdrop is gone, so tapping outside a row's menu opened the
// row beneath it (owner: "when tapping outside to dismiss the modal, the click goes through and
// triggers others"). The press that closes the sheet calls this.

/** Eats the click that finishes the current press, wherever it lands. It lapses at the next
 *  press, and a keyboard or scripted click (`detail` 0) is never eaten. */
export function swallowNextClick(): void {
  const swallow = (event: MouseEvent) => {
    if (event.detail === 0) return
    event.preventDefault()
    event.stopPropagation()
    lapse()
  }
  const lapse = () => {
    window.removeEventListener('click', swallow, true)
    window.removeEventListener('pointerdown', lapse, true)
  }
  window.addEventListener('click', swallow, true)
  window.addEventListener('pointerdown', lapse, true)
}
