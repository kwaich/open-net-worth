// @vitest-environment jsdom
//
// A document's pages zoom by widening (the scroller around them pans): double-click toggles
// 2.5×, a trackpad pinch (ctrl + wheel) zooms, and a plain wheel stays the scroller's.
import { afterEach, describe, expect, it } from 'vitest'
import { useRef } from 'react'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { usePinchZoom } from './usePinchZoom'

afterEach(cleanup)

function Pages() {
  const ref = useRef<HTMLDivElement>(null)
  const zoom = usePinchZoom(ref)
  return (
    <div className="ui-modal__body">
      <div ref={ref} data-testid="pages" style={{ width: `${zoom * 100}%` }} />
    </div>
  )
}

describe('usePinchZoom', () => {
  it('a double-click widens the pages 2.5× and a second one puts them back', () => {
    const { getByTestId } = render(<Pages />)
    const pages = getByTestId('pages')
    fireEvent.dblClick(pages, { clientX: 100, clientY: 100 })
    expect(pages.style.width).toBe('250%')
    fireEvent.dblClick(pages, { clientX: 100, clientY: 100 })
    expect(pages.style.width).toBe('100%')
  })

  it('a trackpad pinch zooms; a plain wheel is left to scroll', () => {
    const { getByTestId } = render(<Pages />)
    const pages = getByTestId('pages')
    fireEvent.wheel(pages, { deltaY: -100 })
    expect(pages.style.width).toBe('100%')
    fireEvent.wheel(pages, { ctrlKey: true, deltaY: -100 })
    expect(parseFloat(pages.style.width)).toBeGreaterThan(200)
  })
})
