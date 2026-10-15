// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { PageTour } from './PageTour'

const STEPS = [
  { target: 'one', title: 'First thing', body: 'About the first thing.' },
  { target: 'two', title: 'Second thing', body: 'About the second thing.' },
]

afterEach(cleanup)

function renderTour(onClose = vi.fn()) {
  render(
    <>
      <div data-tour="one">one</div>
      <div data-tour="two">two</div>
      <PageTour steps={STEPS} onClose={onClose} />
    </>,
  )
  return onClose
}

it('walks the steps in order and closes on Done', async () => {
  const onClose = renderTour()
  expect(await screen.findByRole('dialog', { name: 'First thing' })).toBeTruthy()
  expect(screen.getByText('Step 1 of 2')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByRole('dialog', { name: 'Second thing' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Back' }))
  expect(await screen.findByRole('dialog', { name: 'First thing' })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Done' }))
  expect(screen.queryByRole('button', { name: 'Skip' })).toBeNull()
  expect(onClose).toHaveBeenCalledOnce()
})

it('Skip and Escape both end the tour', async () => {
  const onClose = renderTour()
  fireEvent.click(await screen.findByRole('button', { name: 'Skip' }))
  fireEvent.keyDown(window, { key: 'Escape' })
  expect(onClose).toHaveBeenCalledTimes(2)
})

it('still explains a step whose element is missing', async () => {
  render(<PageTour steps={[{ target: 'gone', title: 'Nowhere', body: 'b' }]} onClose={vi.fn()} />)
  expect(await screen.findByRole('dialog', { name: 'Nowhere' })).toBeTruthy()
})

it('shows no card until its step is placed, then jumps to a target further down the page', async () => {
  const scrolls: unknown[] = []
  const box = (top: number) =>
    ({ top, bottom: top + 40, left: 20, right: 220, width: 200, height: 40 }) as DOMRect
  render(
    <>
      <div
        data-tour="low"
        ref={(el) => {
          if (!el) return
          el.getBoundingClientRect = () => box(scrolls.length ? 300 : 2000)
          el.scrollIntoView = (opts) => scrolls.push(opts)
        }}
      >
        low
      </div>
      <PageTour steps={[{ target: 'low', title: 'Down here', body: 'b' }]} onClose={vi.fn()} />
    </>,
  )
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(await screen.findByRole('dialog', { name: 'Down here' })).toBeTruthy()
  // An instant jump under the dim, never a smooth scroll the spotlight would chase.
  expect(scrolls).toEqual([{ block: 'center', behavior: 'instant' }])
  expect(document.querySelector<HTMLElement>('.pt-spot')?.style.top).toBe('294px')
})

it('lights one element at a time, never leaving the last step lit behind', async () => {
  const at = (left: number) => (el: HTMLElement | null) => {
    if (el)
      el.getBoundingClientRect = () =>
        ({ top: 300, bottom: 328, left, right: left + 28, width: 28, height: 28 }) as DOMRect
  }
  render(
    <>
      <button data-tour="one" ref={at(20)} />
      <button data-tour="two" ref={at(60)} />
      <PageTour steps={STEPS} onClose={vi.fn()} />
    </>,
  )
  fireEvent.click(await screen.findByRole('button', { name: 'Next' }))
  await screen.findByRole('dialog', { name: 'Second thing' })
  const spots = document.querySelectorAll<HTMLElement>('.pt-spot')
  expect(spots).toHaveLength(1)
  expect(spots[0]!.style.left).toBe('54px')
})

it('a tap outside the card moves on, never skips', async () => {
  const onClose = renderTour()
  await screen.findByRole('dialog', { name: 'First thing' })
  fireEvent.click(document.querySelector('.pt-layer')!)
  expect(await screen.findByRole('dialog', { name: 'Second thing' })).toBeTruthy()
  fireEvent.click(screen.getByText('About the second thing.'))
  expect(screen.getByRole('dialog', { name: 'Second thing' })).toBeTruthy()
  expect(onClose).not.toHaveBeenCalled()
  fireEvent.click(document.querySelector('.pt-layer')!)
  expect(onClose).toHaveBeenCalledOnce()
})

it('a tap on the lit control at the last step closes the tour and uses the control', async () => {
  const onClose = vi.fn()
  const used = vi.fn()
  render(
    <>
      <button
        data-tour="only"
        onClick={used}
        ref={(el) => {
          if (el)
            el.getBoundingClientRect = () =>
              ({ top: 300, bottom: 328, left: 20, right: 120, width: 100, height: 28 }) as DOMRect
        }}
      />
      <PageTour steps={[{ target: 'only', title: 'Add one', body: 'b' }]} onClose={onClose} />
    </>,
  )
  await screen.findByRole('dialog', { name: 'Add one' })
  fireEvent.click(document.querySelector('.pt-layer')!, { clientX: 60, clientY: 310 })
  expect(onClose).toHaveBeenCalledOnce()
  await vi.waitFor(() => expect(used).toHaveBeenCalledOnce())
})
