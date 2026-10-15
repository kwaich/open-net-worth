import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useIsCoarsePointer } from '../../hooks/useIsCoarsePointer'
import { InfoPopover } from '../ui'

// A pulse cell or flag that explains itself. On a mouse it shows its own tooltip almost at once
// — the native `title` tooltip waits about a second, which read as broken on a dense desk. A
// touch screen has no hover, so there the same element opens it as a popover on tap.

const SHOW_DELAY_MS = 120
const TIP_WIDTH = 280
const EDGE = 8
const GAP = 6
const ROOM_BELOW = 140 // flip above when less than this is left under the trigger

interface Place {
  top: number
  left: number
  above: boolean
}

function HoverExplain({
  text,
  className,
  label,
  role,
  children,
}: {
  text: string
  className: string
  label?: string
  role?: 'img'
  children: ReactNode
}) {
  const ref = useRef<HTMLSpanElement>(null)
  const timer = useRef<number | undefined>(undefined)
  const [place, setPlace] = useState<Place | null>(null)
  const id = useId()

  const hide = () => {
    window.clearTimeout(timer.current)
    setPlace(null)
  }
  const show = () => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      const r = ref.current?.getBoundingClientRect()
      if (!r) return
      const above = window.innerHeight - r.bottom < ROOM_BELOW && r.top > ROOM_BELOW
      const left = Math.max(EDGE, Math.min(r.left, window.innerWidth - TIP_WIDTH - EDGE))
      setPlace({ top: above ? r.top - GAP : r.bottom + GAP, left, above })
    }, SHOW_DELAY_MS)
  }

  // The bubble is fixed to the viewport (so a card's overflow can't clip it); a scroll would
  // leave it behind, so it simply goes away.
  useEffect(() => {
    if (!place) return
    window.addEventListener('scroll', hide, true)
    return () => window.removeEventListener('scroll', hide, true)
  }, [place])
  useEffect(() => () => window.clearTimeout(timer.current), [])

  return (
    <span
      ref={ref}
      className={className}
      role={role}
      aria-label={label}
      aria-description={text}
      aria-describedby={place ? id : undefined}
      onPointerEnter={(e) => {
        if (e.pointerType === 'mouse') show()
      }}
      onPointerLeave={hide}
    >
      {children}
      {place &&
        createPortal(
          <span
            id={id}
            role="tooltip"
            className={`explain-tip${place.above ? ' explain-tip--above' : ''}`}
            style={{ top: place.top, left: place.left, maxWidth: TIP_WIDTH }}
          >
            {text}
          </span>,
          document.body,
        )}
    </span>
  )
}

export function Explain({
  text,
  className,
  label,
  role,
  children,
}: {
  text: string
  className: string
  /** Accessible name when the visible content doesn't say enough (the trend line). */
  label?: string
  role?: 'img'
  children: ReactNode
}) {
  const coarse = useIsCoarsePointer()
  if (coarse) {
    return (
      <InfoPopover text={text} triggerClassName={className} label={label} width={260}>
        {children}
      </InfoPopover>
    )
  }
  return (
    <HoverExplain text={text} className={className} label={label} role={role}>
      {children}
    </HoverExplain>
  )
}
