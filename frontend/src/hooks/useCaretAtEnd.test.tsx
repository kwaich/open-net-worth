// @vitest-environment jsdom
//
// A field opened to edit its text starts with the caret after the last character — iOS puts a
// field focused in code at the first one. Only the first focus: a later tap keeps its place.
import { afterEach, describe, expect, it } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { useCaretAtEnd } from './useCaretAtEnd'

afterEach(cleanup)

function Field({ value }: { value: string }) {
  const caretAtEnd = useCaretAtEnd()
  return <textarea aria-label="Note" defaultValue={value} onFocus={caretAtEnd} />
}

const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve))

describe('useCaretAtEnd', () => {
  it('puts the caret after the last character the first time the field takes focus', async () => {
    render(<Field value={'Signed copy\nThe original is in the safe'} />)
    const field = screen.getByLabelText('Note') as HTMLTextAreaElement
    field.setSelectionRange(0, 0) // where iOS leaves a field focused in code
    act(() => field.focus())
    const end = field.value.length
    await waitFor(() => expect([field.selectionStart, field.selectionEnd]).toEqual([end, end]))
  })

  it('leaves the caret where a later tap puts it', async () => {
    render(<Field value="Hello" />)
    const field = screen.getByLabelText('Note') as HTMLTextAreaElement
    field.setSelectionRange(0, 0)
    act(() => field.focus())
    await waitFor(() => expect(field.selectionStart).toBe(5))
    act(() => field.blur())
    field.setSelectionRange(2, 2)
    act(() => field.focus())
    await nextFrame()
    expect(field.selectionStart).toBe(2)
  })
})
