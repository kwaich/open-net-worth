// @vitest-environment jsdom

import { cleanup, fireEvent, render } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { usePointerResize } from './usePointerResize'

function ResizeHandle({
  onStart = vi.fn(),
  onMove = vi.fn(),
  onEnd = vi.fn(),
}: {
  onStart?: (event: PointerEvent) => void
  onMove?: (event: PointerEvent) => void
  onEnd?: (event: PointerEvent) => void
}) {
  const onPointerDown = usePointerResize({ onStart, onMove, onEnd })
  return <div data-testid="resize-handle" onPointerDown={onPointerDown} />
}

afterEach(() => {
  cleanup()
  document.body.style.userSelect = ''
})

describe('usePointerResize', () => {
  it('captures a primary pointer and starts the resize', () => {
    const onStart = vi.fn()
    const { getByTestId } = render(<ResizeHandle onStart={onStart} />)
    const handle = getByTestId('resize-handle')
    const capture = vi.fn()
    Object.defineProperty(handle, 'setPointerCapture', { configurable: true, value: capture })

    fireEvent.pointerDown(handle, {
      pointerId: 7,
      pointerType: 'pen',
      isPrimary: true,
      clientX: 120,
    })

    expect(capture).toHaveBeenCalledWith(7)
    expect(onStart).toHaveBeenCalledOnce()
    expect(onStart).toHaveBeenCalledWith(
      expect.objectContaining({ pointerId: 7, pointerType: 'pen', clientX: 120 }),
    )
    expect(document.body.style.userSelect).toBe('none')
  })

  it('ignores a non-primary pointer', () => {
    const onStart = vi.fn()
    const { getByTestId } = render(<ResizeHandle onStart={onStart} />)
    const handle = getByTestId('resize-handle')
    const capture = vi.fn()
    Object.defineProperty(handle, 'setPointerCapture', { configurable: true, value: capture })

    fireEvent.pointerDown(handle, { pointerId: 2, pointerType: 'touch', isPrimary: false })

    expect(capture).not.toHaveBeenCalled()
    expect(onStart).not.toHaveBeenCalled()
    expect(document.body.style.userSelect).toBe('')
  })

  it('delivers moves for the captured pointer only', () => {
    const onMove = vi.fn()
    const { getByTestId } = render(<ResizeHandle onMove={onMove} />)
    const handle = getByTestId('resize-handle')
    Object.defineProperty(handle, 'setPointerCapture', { configurable: true, value: vi.fn() })

    fireEvent.pointerDown(handle, { pointerId: 3, pointerType: 'touch', isPrimary: true })
    fireEvent.pointerMove(handle, { pointerId: 4, clientX: 80 })
    fireEvent.pointerMove(handle, { pointerId: 3, clientX: 180 })

    expect(onMove).toHaveBeenCalledOnce()
    expect(onMove).toHaveBeenCalledWith(expect.objectContaining({ pointerId: 3, clientX: 180 }))
  })

  it('ends and cleans up when the captured pointer is cancelled', () => {
    const onEnd = vi.fn()
    const onMove = vi.fn()
    const { getByTestId } = render(<ResizeHandle onEnd={onEnd} onMove={onMove} />)
    const handle = getByTestId('resize-handle')
    const release = vi.fn()
    Object.defineProperty(handle, 'setPointerCapture', { configurable: true, value: vi.fn() })
    Object.defineProperty(handle, 'releasePointerCapture', { configurable: true, value: release })
    document.body.style.userSelect = 'text'

    fireEvent.pointerDown(handle, { pointerId: 5, pointerType: 'touch', isPrimary: true })
    fireEvent.pointerCancel(handle, { pointerId: 5 })
    fireEvent.pointerMove(handle, { pointerId: 5, clientX: 200 })

    expect(onEnd).toHaveBeenCalledOnce()
    expect(onEnd).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'pointercancel', pointerId: 5 }),
    )
    expect(release).toHaveBeenCalledWith(5)
    expect(onMove).not.toHaveBeenCalled()
    expect(document.body.style.userSelect).toBe('text')
  })

  it('ends on lost pointer capture so a replacement drag can start', () => {
    const onStart = vi.fn()
    const onEnd = vi.fn()
    const { getByTestId } = render(<ResizeHandle onStart={onStart} onEnd={onEnd} />)
    const handle = getByTestId('resize-handle')
    Object.defineProperty(handle, 'setPointerCapture', { configurable: true, value: vi.fn() })
    Object.defineProperty(handle, 'releasePointerCapture', { configurable: true, value: vi.fn() })

    fireEvent.pointerDown(handle, { pointerId: 11, pointerType: 'touch', isPrimary: true })
    fireEvent.lostPointerCapture(handle, { pointerId: 11 })
    fireEvent.pointerDown(handle, { pointerId: 12, pointerType: 'touch', isPrimary: true })

    expect(onEnd).toHaveBeenCalledOnce()
    expect(onStart).toHaveBeenCalledTimes(2)
  })
})
