// @vitest-environment jsdom
//
// A photo zooms in place (the app has the browser's pinch-zoom off): double-click toggles 2.5×,
// a trackpad pinch (ctrl + wheel) zooms, and it never zooms out past the whole photo.
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render } from '@testing-library/react'
import { ZoomImage } from './ZoomImage'

afterEach(cleanup)

const scaleOf = (img: HTMLElement) => Number(/scale\(([\d.]+)\)/.exec(img.style.transform)?.[1])

function renderPhoto() {
  const { container } = render(<ZoomImage src="/photo.jpg" alt="Receipt" />)
  const frame = container.querySelector('.ui-zoomimage') as HTMLElement
  Object.defineProperty(frame, 'clientWidth', { value: 300 })
  Object.defineProperty(frame, 'clientHeight', { value: 400 })
  return { frame, img: frame.querySelector('img') as HTMLImageElement }
}

describe('ZoomImage', () => {
  it('fits whole at rest, owns its touches, and a double-click toggles 2.5×', () => {
    const { frame, img } = renderPhoto()
    expect(scaleOf(img)).toBe(1)
    expect(frame.hasAttribute('data-sheet-drag-ignore')).toBe(true)
    fireEvent.dblClick(frame, { clientX: 150, clientY: 200 })
    expect(scaleOf(img)).toBe(2.5)
    expect(frame.classList.contains('is-zoomed')).toBe(true)
    fireEvent.dblClick(frame, { clientX: 150, clientY: 200 })
    expect(scaleOf(img)).toBe(1)
  })

  it('a trackpad pinch zooms in, and never out past the whole photo', () => {
    const { frame, img } = renderPhoto()
    fireEvent.wheel(frame, { ctrlKey: true, deltaY: -100, clientX: 10, clientY: 10 })
    expect(scaleOf(img)).toBeGreaterThan(2)
    for (let i = 0; i < 5; i++) fireEvent.wheel(frame, { ctrlKey: true, deltaY: 200 })
    expect(scaleOf(img)).toBe(1)
    expect(img.style.transform).toBe('translate(0px, 0px) scale(1)')
  })
})
