import { describe, expect, it } from 'vitest'
import { placeBelowOrAbove } from './anchoredPopover'

const VIEW = { width: 800, height: 600 }
const POP = { width: 260, height: 190 }
const trigger = (top: number, right = 780) => ({ top, bottom: top + 20, right })

describe('placeBelowOrAbove', () => {
  it('opens below the trigger, right edges aligned, when there is room', () => {
    expect(placeBelowOrAbove(trigger(100), POP, VIEW)).toEqual({
      top: 124,
      left: 520,
      above: false,
    })
  })

  it('opens above the trigger when the window has no room below — the last message', () => {
    const p = placeBelowOrAbove(trigger(389), POP, VIEW)
    expect(p.above).toBe(true)
    expect(p.top).toBe(389 - 4 - 190)
    expect(p.top + POP.height).toBeLessThanOrEqual(389)
  })

  it('stays below when there is even less room above', () => {
    const p = placeBelowOrAbove(trigger(60), POP, { width: 800, height: 260 })
    expect(p.above).toBe(false)
  })

  it('is clamped inside the window when it fits neither way', () => {
    const p = placeBelowOrAbove(trigger(150), POP, { width: 800, height: 320 })
    expect(p.top).toBeGreaterThanOrEqual(8)
    expect(p.top + POP.height).toBeLessThanOrEqual(320 - 8)
  })

  it('never runs off the left or right edge', () => {
    expect(placeBelowOrAbove(trigger(100, 120), POP, VIEW).left).toBe(8)
    expect(placeBelowOrAbove(trigger(100, 900), POP, VIEW).left).toBe(800 - 260 - 8)
  })
})
