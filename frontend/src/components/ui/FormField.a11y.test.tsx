// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { FormField } from './FormField'

describe('FormField a11y — aria-describedby + aria-invalid wiring', () => {
  it('passes aria-describedby to the child when error is present', () => {
    const html = renderToStaticMarkup(
      <FormField htmlFor="name" error="Required">
        <input id="name" />
      </FormField>,
    )
    // The input must carry aria-describedby pointing at the error element id.
    expect(html).toContain('aria-describedby="name-error"')
  })

  it('passes aria-invalid to the child when error is present', () => {
    const html = renderToStaticMarkup(
      <FormField htmlFor="name" error="Required">
        <input id="name" />
      </FormField>,
    )
    expect(html).toContain('aria-invalid="true"')
  })

  it('error element carries the matching id', () => {
    const html = renderToStaticMarkup(
      <FormField htmlFor="name" error="Required">
        <input id="name" />
      </FormField>,
    )
    expect(html).toContain('id="name-error"')
  })

  it('passes aria-describedby to the child when help is present (no error)', () => {
    const html = renderToStaticMarkup(
      <FormField htmlFor="email" help="Your email address">
        <input id="email" />
      </FormField>,
    )
    expect(html).toContain('aria-describedby="email-help"')
  })

  it('does NOT set aria-invalid when only help is present', () => {
    const html = renderToStaticMarkup(
      <FormField htmlFor="email" help="Your email address">
        <input id="email" />
      </FormField>,
    )
    expect(html).not.toContain('aria-invalid')
  })

  it('help element carries the matching id', () => {
    const html = renderToStaticMarkup(
      <FormField htmlFor="email" help="Your email address">
        <input id="email" />
      </FormField>,
    )
    expect(html).toContain('id="email-help"')
  })

  it('does NOT add aria-describedby when neither error nor help is given', () => {
    const html = renderToStaticMarkup(
      <FormField htmlFor="qty">
        <input id="qty" />
      </FormField>,
    )
    expect(html).not.toContain('aria-describedby')
    expect(html).not.toContain('aria-invalid')
  })

  it('can reserve validation-message space without exposing hidden text', () => {
    const html = renderToStaticMarkup(
      <FormField htmlFor="name" reserveMessageSpace>
        <input id="name" />
      </FormField>,
    )

    expect(html).toContain('ui-field__message-space')
    expect(html).toContain('aria-hidden="true"')
    expect(html).not.toContain('aria-describedby')
  })
})
