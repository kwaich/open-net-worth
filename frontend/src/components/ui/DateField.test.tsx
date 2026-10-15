// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { DateField } from './DateField'

afterEach(cleanup)

describe('DateField', () => {
  it('marks an empty date, so its mm/dd/yyyy mask can fade instead of reading as picked', () => {
    const { rerender } = render(<DateField aria-label="Bought on" value="" onChange={() => {}} />)
    const input = screen.getByLabelText('Bought on')
    expect(input.hasAttribute('data-empty')).toBe(true)
    rerender(<DateField aria-label="Bought on" value="2019-06-15" onChange={() => {}} />)
    expect(input.hasAttribute('data-empty')).toBe(false)
  })

  it('says nothing about emptiness when the caller does not control its value', () => {
    render(<DateField aria-label="Bought on" defaultValue="" />)
    expect(screen.getByLabelText('Bought on').hasAttribute('data-empty')).toBe(false)
  })

  it("pins its height only where the kit draws the box — never inside a caller's own wrapper", () => {
    render(
      <>
        <DateField aria-label="Kit" value="" onChange={() => {}} />
        <DateField
          aria-label="Own box"
          className="acquire-lot-datefield-input"
          wrapperClassName="acquire-lot-datefield"
          value=""
          onChange={() => {}}
        />
        <DateField aria-label="Unstyled" unstyled value="" onChange={() => {}} />
      </>,
    )
    const boxed = (label: string) =>
      screen.getByLabelText(label).parentElement!.classList.contains('ui-datefield--boxed')
    expect(boxed('Kit')).toBe(true)
    expect(boxed('Own box')).toBe(false)
    expect(boxed('Unstyled')).toBe(false)

    const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8')
    expect(css).toMatch(/\.ui-datefield--boxed > input\.ui-input \{\s*height: calc\(1lh/)
    expect(css).not.toMatch(/\.ui-datefield > input\.ui-input \{\s*height/)
  })
})
