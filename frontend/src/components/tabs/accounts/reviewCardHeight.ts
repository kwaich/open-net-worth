import { useLayoutEffect, type RefObject } from 'react'

const RESIZE_MS = 240
const RESIZE_EASING = 'cubic-bezier(0.22, 0.8, 0.3, 1)'

function canAnimate(card: HTMLElement): boolean {
  if (typeof card.animate !== 'function') return false
  return !(
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/**
 * Animates the front card between its heights instead of letting it snap. A card is sized to
 * its content, so the next transaction (a memo line more, a chip row fewer) and the category
 * search opening both used to jump the card, the hint and the modal by the difference in one
 * frame (owner report 2026-09-20).
 *
 * A FLIP on the card's own box: play from the height it had to the height it has, then hand
 * back to the natural height — no inline height is left behind. While it plays,
 * `data-resizing` clips the card and pins its children at their natural size, so growth reveals
 * the bottom instead of squeezing the body (the squeeze is what once clipped the heading).
 *
 * The card is keyed by transaction, so the height to play FROM is kept by the parent across
 * cards (`lastHeight` / `remember`): a new card starts where the old one left and settles into
 * its own.
 */
export function useSmoothCardHeight(
  cardRef: RefObject<HTMLDivElement | null>,
  lastHeight: () => number | null,
  remember: (height: number) => void,
) {
  useLayoutEffect(() => {
    const card = cardRef.current
    if (!card) return
    let running: Animation | null = null
    const settle = (from: number | null, to: number) => {
      remember(to)
      if (from == null || from === to || !canAnimate(card)) return
      running?.cancel()
      card.dataset.resizing = ''
      running = card.animate([{ height: `${from}px` }, { height: `${to}px` }], {
        duration: RESIZE_MS,
        easing: RESIZE_EASING,
      })
      const done = () => {
        delete card.dataset.resizing
        running = null
        card.dispatchEvent(new Event(RESIZED_EVENT))
      }
      running.onfinish = done
      running.oncancel = done
    }
    settle(lastHeight(), card.offsetHeight)
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      // The sizes the animation itself passes through are not content changes. Whatever the
      // content did meanwhile is picked up when it lands: the box returns to its natural height
      // and, if that differs from the target played to, this fires once more.
      if (running?.playState === 'running') return
      settle(lastHeight(), card.offsetHeight)
    })
    observer.observe(card)
    return () => {
      observer.disconnect()
      running?.cancel()
    }
  }, [cardRef, lastHeight, remember])
}

const LIST_MAX_PX = 224
const LIST_MIN_PX = 96
/** Fired on the card by useSmoothCardHeight when a height play lands, so a measurement that
 *  waited it out can run against the settled box. */
const RESIZED_EVENT = 'acct-review-card-resized'

function inFlow(el: Element): boolean {
  const position = getComputedStyle(el).position
  return position !== 'absolute' && position !== 'fixed'
}

/**
 * Keeps the card inside the stage by lowering the category / account list's cap — the one
 * thing on the card that may give. The list prefers 224px; when the stage cannot fit the card
 * at that, the cap drops by the shortfall, to a floor of about two rows (96px); past that the
 * card overflows its slot and the stage scrolls it, as it always did. Nothing else on the card
 * ever shrinks: a card that squeezes its own body once clipped the heading mid-glyph.
 *
 * Measured, not styled, because CSS cannot express "prefer 224, never below 96" for a list
 * inside a content-sized card — Chromium sizes a column flexbox's minimum from its items'
 * preferred sizes, so a shrinkable card either never shrank or shrank past the floor.
 *
 * The room is the stage's height PLUS whatever the dialog could still grow by (on desktop the
 * stage is content-sized up to the dialog's max-height, so its current height would only echo
 * the cap already applied), MINUS everything that is not the list. Measured against the
 * settled box only: mid-play the card's height is the animation's, not the content's.
 */
export function useListFitsStage(cardRef: RefObject<HTMLDivElement | null>) {
  useLayoutEffect(() => {
    const card = cardRef.current
    const stage = card?.closest<HTMLElement>('.acct-review-stage')
    const dialog = stage?.closest<HTMLElement>('.ui-modal-card')
    if (!card || !stage || !dialog || typeof ResizeObserver === 'undefined') return
    const fit = () => {
      if (card.dataset.resizing !== undefined) return
      const list = card.querySelector<HTMLElement>('.acct-review-category-results')
      if (!list) {
        card.style.removeProperty('--acct-review-list-max')
        return
      }
      const stageStyle = getComputedStyle(stage)
      const deck = card.parentElement as HTMLElement
      let others =
        card.offsetHeight - list.offsetHeight + parseFloat(getComputedStyle(deck).marginBottom)
      let items = 1
      for (const child of stage.children) {
        if (child === deck || !inFlow(child)) continue
        others += (child as HTMLElement).offsetHeight
        items += 1
      }
      others +=
        parseFloat(stageStyle.paddingTop) +
        parseFloat(stageStyle.paddingBottom) +
        (parseFloat(stageStyle.rowGap) || 0) * (items - 1)
      const dialogMax = parseFloat(getComputedStyle(dialog).maxHeight)
      const slack = Number.isFinite(dialogMax) ? Math.max(0, dialogMax - dialog.offsetHeight) : 0
      const room = stage.clientHeight + slack - others
      const cap = Math.max(LIST_MIN_PX, Math.min(LIST_MAX_PX, Math.round(room)))
      card.style.setProperty('--acct-review-list-max', `${cap}px`)
    }
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(card)
    observer.observe(stage)
    card.addEventListener(RESIZED_EVENT, fit)
    return () => {
      observer.disconnect()
      card.removeEventListener(RESIZED_EVENT, fit)
    }
  }, [cardRef])
}
