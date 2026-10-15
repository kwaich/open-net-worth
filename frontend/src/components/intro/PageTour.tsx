// A short spotlight tour of a page: one element lit at a time, a card beside it saying what it
// is. Targets are `data-tour="<id>"` attributes, so a page opts elements in without refs.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { createPortal } from 'react-dom'
import { Button } from '../ui'
import { findTarget, markGuided, tourShowing, visibleRect } from './tourTarget'
import './intro.css'

export interface TourStep {
  /** Matches an element's `data-tour` attribute. */
  target: string
  title: string
  body: string
}

const CARD_W = 320
const GAP = 12
const PAD = 6
/** Room the card needs beside the lit element. */
const CARD_ROOM = 200

type Placement = 'below' | 'above' | 'free'

function cardPosition(rect: DOMRect | null): { style: CSSProperties; placement: Placement } {
  const width = Math.min(CARD_W, window.innerWidth - 32)
  if (!rect) {
    const left = (window.innerWidth - width) / 2
    return { style: { width, left, top: window.innerHeight / 3 }, placement: 'free' }
  }
  const left = Math.min(Math.max(rect.left, 16), window.innerWidth - width - 16)
  // The pointer sits under the lit element's left side, kept inside the card's corners.
  const arrowX = Math.min(Math.max(rect.left + 28 - left, 18), width - 30)
  const style = { width, left, ['--arrow-x' as string]: `${arrowX}px` }
  const below = rect.bottom + PAD + GAP
  return window.innerHeight - below > CARD_ROOM
    ? { style: { ...style, top: below }, placement: 'below' }
    : {
        style: { ...style, bottom: window.innerHeight - rect.top + PAD + GAP },
        placement: 'above',
      }
}

export function PageTour({ steps, onClose }: { steps: TourStep[]; onClose: () => void }) {
  const [i, setI] = useState(0)
  const [measured, setMeasured] = useState<{ target: string; rect: DOMRect | null } | null>(null)
  const cardRef = useRef<HTMLDivElement>(null)
  const step = steps[i]!
  const last = i === steps.length - 1
  // Until this step is measured only the dim shows, so its card never flashes up somewhere
  // else first. A step whose target is not on the page gets a free-standing card.
  const settled = measured?.target === step.target
  const rect = settled ? measured.rect : null

  useEffect(markGuided, [])
  useEffect(tourShowing, [])

  useLayoutEffect(() => {
    const el = findTarget(step.target, 'page')
    if (!el) {
      const raf = requestAnimationFrame(() => setMeasured({ target: step.target, rect: null }))
      return () => cancelAnimationFrame(raf)
    }
    // Brought into view at once, under the dim: a smooth scroll made the lit box chase the
    // page down a phone screen. Left where it is when it and its card already fit.
    const r = el.getBoundingClientRect()
    const fits =
      r.top >= 8 &&
      r.bottom <= window.innerHeight - 8 &&
      Math.max(r.top, window.innerHeight - r.bottom) > CARD_ROOM
    if (!fits) el.scrollIntoView?.({ block: 'center', behavior: 'instant' as ScrollBehavior })
    let raf = 0
    const measure = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => setMeasured({ target: step.target, rect: visibleRect(el) }))
    }
    measure()
    const resized = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    resized?.observe(el)
    window.addEventListener('resize', measure)
    window.addEventListener('scroll', measure, true)
    return () => {
      cancelAnimationFrame(raf)
      resized?.disconnect()
      window.removeEventListener('resize', measure)
      window.removeEventListener('scroll', measure, true)
    }
  }, [step.target])

  // The page may focus something of its own as it settles (a composer autofocuses), so take
  // focus on the next frame too.
  useEffect(() => {
    const focusNext = () => cardRef.current?.querySelector<HTMLElement>('[data-tour-next]')?.focus()
    focusNext()
    const raf = requestAnimationFrame(focusNext)
    return () => cancelAnimationFrame(raf)
  }, [i, settled])

  const next = useCallback(() => (last ? onClose() : setI((n) => n + 1)), [last, onClose])
  const back = useCallback(() => setI((n) => Math.max(0, n - 1)), [])
  // The tour owns its keys while it is up, in the capture phase, so they never reach the page
  // underneath (an Enter must not send what a composer holds).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const act =
        e.key === 'Escape'
          ? onClose
          : e.key === 'Enter' || e.key === 'ArrowRight'
            ? next
            : e.key === 'ArrowLeft'
              ? back
              : null
      if (!act) return
      e.preventDefault()
      e.stopPropagation()
      act()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [next, back, onClose])

  const pos = cardPosition(rect)
  return createPortal(
    // A tap anywhere but the card moves on, as a phone user expects. Never skips: the tour
    // shows once, so a stray tap must not lose it, and Back undoes an extra step. On the last
    // step a tap on the lit control also does what that control does ("Add your first
    // account" opens the add dialog) instead of only closing the tour.
    <div
      className="pt-layer"
      onClick={(e) => {
        if (cardRef.current?.contains(e.target as Node)) return
        const onLit =
          rect &&
          e.clientX >= rect.left - PAD &&
          e.clientX <= rect.right + PAD &&
          e.clientY >= rect.top - PAD &&
          e.clientY <= rect.bottom + PAD
        if (!(last && onLit)) return next()
        const target = step.target
        onClose()
        setTimeout(() => findTarget(target, 'page')?.click(), 0)
      }}
    >
      {rect ? (
        <div
          key={'spot:' + step.target}
          className="pt-spot"
          style={{
            top: rect.top - PAD,
            left: rect.left - PAD,
            width: rect.width + PAD * 2,
            height: rect.height + PAD * 2,
          }}
        />
      ) : (
        <div className="pt-dim" />
      )}
      {settled && (
        <div
          key={'card:' + step.target}
          ref={cardRef}
          className={`pt-card pt-card--${pos.placement}`}
          role="dialog"
          aria-modal="true"
          aria-labelledby="pt-title"
          style={pos.style}
        >
          <h3 id="pt-title" className="pt-title">
            {step.title}
          </h3>
          <p className="pt-body">{step.body}</p>
          <div className="pt-foot">
            <span className="pt-dots">
              <span className="ui-visually-hidden">
                Step {i + 1} of {steps.length}
              </span>
              {steps.map((s, n) => (
                <span
                  key={s.target}
                  aria-hidden="true"
                  className={n === i ? 'pt-dot pt-dot--on' : 'pt-dot'}
                />
              ))}
            </span>
            <span className="pt-actions">
              {!last && (
                <button type="button" className="pt-skip" onClick={onClose}>
                  Skip
                </button>
              )}
              {i > 0 && (
                <Button size="sm" variant="secondary" onClick={back}>
                  Back
                </Button>
              )}
              <Button data-tour-next size="sm" variant="primary" onClick={next}>
                {last ? 'Done' : 'Next'}
              </Button>
            </span>
          </div>
        </div>
      )}
    </div>,
    document.body,
  )
}
