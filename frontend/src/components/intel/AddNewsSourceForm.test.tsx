// @vitest-environment jsdom

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { AddNewsSourceForm, sourceFieldError, MAX_USER_SOURCES } from './AddNewsSourceForm'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

afterEach(cleanup)
beforeEach(() => mockAuthFetch.mockClear())

describe('sourceFieldError', () => {
  it('routes a label-prefixed message to the label field', () => {
    expect(sourceFieldError('label: is required')).toEqual({ field: 'label' })
  })
  it('routes a url-prefixed message to the url field', () => {
    expect(
      sourceFieldError("url: 'ftp://bad.example.com' is not a safe http(s) URL (scheme)"),
    ).toEqual({
      field: 'url',
    })
  })
  it('falls back to null for an unrecognized prefix', () => {
    expect(
      sourceFieldError('sources: at most 20 user-added sources allowed (already have 20)'),
    ).toEqual({
      field: null,
    })
  })
})

describe('AddNewsSourceForm', () => {
  it('renders the label/url fields and their exact copy', () => {
    const { getByLabelText, getByPlaceholderText, getByText } = render(
      <AddNewsSourceForm userCount={0} onAdded={vi.fn()} />,
    )
    expect(getByLabelText('Feed name')).toBeTruthy()
    expect(getByPlaceholderText('e.g. Reuters Markets')).toBeTruthy()
    expect(getByLabelText('Feed URL')).toBeTruthy()
    expect(getByPlaceholderText('https://example.com/feed.xml')).toBeTruthy()
    expect(
      getByText('Must be a public http(s) RSS/Atom feed — Lucius checks it before saving.'),
    ).toBeTruthy()
    expect(
      getByText('Lucius checks this feed on its own schedule, alongside your other sources.'),
    ).toBeTruthy()
    expect(getByText('Add feed')).toBeTruthy()
  })

  it('disables Add feed until both fields have content', () => {
    const { getByText, getByLabelText } = render(
      <AddNewsSourceForm userCount={0} onAdded={vi.fn()} />,
    )
    const button = getByText('Add feed') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    fireEvent.change(getByLabelText('Feed name'), { target: { value: 'Reuters Markets' } })
    expect(button.disabled).toBe(true)
    fireEvent.change(getByLabelText('Feed URL'), {
      target: { value: 'https://reuters.com/feed.xml' },
    })
    expect(button.disabled).toBe(false)
  })

  it('happy path: POSTs {label, url} and calls onAdded, clearing the fields', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ id: 'src_new1' }),
    })
    const onAdded = vi.fn()
    const { getByText, getByLabelText } = render(
      <AddNewsSourceForm userCount={0} onAdded={onAdded} />,
    )
    fireEvent.change(getByLabelText('Feed name'), { target: { value: 'Reuters Markets' } })
    fireEvent.change(getByLabelText('Feed URL'), {
      target: { value: 'https://reuters.com/feed.xml' },
    })
    fireEvent.click(getByText('Add feed'))
    await waitFor(() => expect(onAdded).toHaveBeenCalled())
    const call = mockAuthFetch.mock.calls[0] as [string, RequestInit]
    const [url, opts] = call
    expect(url).toBe('/api/intel/sources')
    expect(opts.method).toBe('POST')
    expect(JSON.parse(opts.body as string)).toEqual({
      label: 'Reuters Markets',
      url: 'https://reuters.com/feed.xml',
    })
    expect((getByLabelText('Feed name') as HTMLInputElement).value).toBe('')
  })

  it('a 422 on label shows the verbatim message under Feed name', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: 'label: is required' }),
    })
    const { getByText, getByLabelText } = render(
      <AddNewsSourceForm userCount={0} onAdded={vi.fn()} />,
    )
    fireEvent.change(getByLabelText('Feed name'), { target: { value: 'x' } })
    fireEvent.change(getByLabelText('Feed URL'), { target: { value: 'https://x.com/feed.xml' } })
    fireEvent.click(getByText('Add feed'))
    await waitFor(() => expect(getByText('label: is required')).toBeTruthy())
  })

  it('a 422 on url shows the verbatim message and a red border on the URL field', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({
        detail: "url: 'ftp://bad.example.com' is not a safe http(s) URL (scheme)",
      }),
    })
    const { getByText, getByLabelText } = render(
      <AddNewsSourceForm userCount={0} onAdded={vi.fn()} />,
    )
    fireEvent.change(getByLabelText('Feed name'), { target: { value: 'Bad feed' } })
    fireEvent.change(getByLabelText('Feed URL'), { target: { value: 'ftp://bad.example.com' } })
    fireEvent.click(getByText('Add feed'))
    await waitFor(() =>
      expect(
        getByText("url: 'ftp://bad.example.com' is not a safe http(s) URL (scheme)"),
      ).toBeTruthy(),
    )
    const urlInput = getByLabelText('Feed URL') as HTMLInputElement
    expect(urlInput.getAttribute('aria-invalid')).toBe('true')
  })

  it('a 422 with no field prefix (the 20-feed cap) shows the server detail VERBATIM, not the generic banner', async () => {
    mockAuthFetch.mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({
        detail: 'sources: at most 20 user-added sources allowed (already have 20)',
      }),
    })
    const { getByText, getByLabelText, queryByText } = render(
      <AddNewsSourceForm userCount={19} onAdded={vi.fn()} />,
    )
    fireEvent.change(getByLabelText('Feed name'), { target: { value: 'One too many' } })
    fireEvent.change(getByLabelText('Feed URL'), { target: { value: 'https://x.com/feed.xml' } })
    fireEvent.click(getByText('Add feed'))
    await waitFor(() =>
      expect(
        getByText('sources: at most 20 user-added sources allowed (already have 20)'),
      ).toBeTruthy(),
    )
    expect(queryByText('Could not add — try again.')).toBeFalsy()
  })

  it('a non-field failure (500) shows the generic "Could not add" banner and keeps values', async () => {
    mockAuthFetch.mockResolvedValue({ ok: false, status: 500, json: async () => ({}) })
    const { getByText, getByLabelText } = render(
      <AddNewsSourceForm userCount={0} onAdded={vi.fn()} />,
    )
    fireEvent.change(getByLabelText('Feed name'), { target: { value: 'Reuters Markets' } })
    fireEvent.change(getByLabelText('Feed URL'), {
      target: { value: 'https://reuters.com/feed.xml' },
    })
    fireEvent.click(getByText('Add feed'))
    await waitFor(() => expect(getByText('Could not add — try again.')).toBeTruthy())
    expect((getByLabelText('Feed name') as HTMLInputElement).value).toBe('Reuters Markets')
  })

  it('at the 20-feed cap, hides the form and shows the muted cap note instead', () => {
    const { getByText, queryByLabelText } = render(
      <AddNewsSourceForm userCount={MAX_USER_SOURCES} onAdded={vi.fn()} />,
    )
    expect(queryByLabelText('Feed name')).toBeFalsy()
    expect(getByText(/reached the limit of 20 feeds/)).toBeTruthy()
  })
})
