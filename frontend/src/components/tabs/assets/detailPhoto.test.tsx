// @vitest-environment jsdom
//
// The asset's photo on its page (owner 2026-09-28): big, and tapping it OPENS it — changing or
// removing it lives in that viewer and in ⋯, never behind the tap. With no photo, the category
// icon is still how you add one. Against a mocked authFetch; synthetic data only.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { OwnedAsset } from '../../../types/assets'
import { AssetDetail } from './detail'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

function asset(overrides: Partial<OwnedAsset> = {}): OwnedAsset {
  return {
    id: 'oa_test000001',
    name: 'Test Apartment',
    category: 'property',
    description: '',
    photo_ref: 'att_photo00001',
    facts: [],
    notes: '',
    cost: 400000,
    cost_date: '2025-01-15',
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 478808,
    valued_on: '2026-09-26',
    share_value: 478808,
    marks: [],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2026-09-26T00:00:00Z',
    updated_at: '2026-09-26T00:00:00Z',
    ...overrides,
  }
}

function renderPage(overrides: Partial<OwnedAsset> = {}) {
  const onPhoto = vi.fn()
  const onToast = vi.fn()
  const utils = render(
    <AssetDetail
      a={asset(overrides)}
      cats={[]}
      loans={[]}
      onBack={vi.fn()}
      onDispose={vi.fn()}
      onDelete={vi.fn()}
      onPhoto={onPhoto}
      onNav={vi.fn()}
      onToast={onToast}
    />,
  )
  return { onPhoto, onToast, container: utils.container }
}

const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => data }) as Response

beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

describe('AssetDetail — the photo', () => {
  it('shows a photo big, and tapping it opens it — never a file picker', () => {
    const { container } = renderPage()
    const photo = screen.getByRole('button', { name: 'Open the photo of Test Apartment' })
    expect(photo.querySelector('img')?.getAttribute('src')).toBe(
      '/api/uploads/att_photo00001/image',
    )
    expect(photo.closest('label')).toBeNull()
    expect(container.querySelector('.as-dhead input[type="file"]')).toBeNull()
    fireEvent.click(photo)
    expect(document.querySelector('.ui-modal__title')?.textContent).toBe('Test Apartment')
    expect(document.querySelector('.as-photo-view img')?.getAttribute('src')).toBe(
      '/api/uploads/att_photo00001/image',
    )
    expect(screen.getByRole('button', { name: 'Change photo' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Remove photo' })).toBeTruthy()
  })

  it('removes the photo only after asking, then says Photo removed.', async () => {
    authFetch.mockResolvedValueOnce(ok(asset({ photo_ref: null })))
    const { onToast } = renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Open the photo of Test Apartment' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove photo' }))
    expect(screen.getByText('Remove this photo?')).toBeTruthy()
    expect(authFetch).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('Photo removed.'))
    const [path, init] = authFetch.mock.calls[0]!
    expect(path).toBe('/api/assets/oa_test000001/photo')
    expect((init as RequestInit).method).toBe('DELETE')
  })

  it('changes it from the viewer through the page picker, which hands on the chosen file', () => {
    const { onPhoto, container } = renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Open the photo of Test Apartment' }))
    const picker = container.parentElement!.querySelector(
      'input[type="file"][accept="image/*"]',
    ) as HTMLInputElement
    const click = vi.spyOn(picker, 'click')
    fireEvent.click(screen.getByRole('button', { name: 'Change photo' }))
    expect(click).toHaveBeenCalledTimes(1)
    const file = new File(['png'], 'new.png', { type: 'image/png' })
    fireEvent.change(picker, { target: { files: [file] } })
    expect(onPhoto).toHaveBeenCalledWith('oa_test000001', file)
  })

  it('with no photo, the same place holds the category and adds one — nothing moves', () => {
    // Owner 2026-09-28: "The layout shouldn't change when there is no image. The image size
    // should be the same."
    const { container, onPhoto } = renderPage({ photo_ref: null })
    expect(screen.queryByRole('button', { name: /Open the photo/ })).toBeNull()
    const slot = screen.getByRole('button', { name: 'Add a photo' })
    expect(slot.classList.contains('as-dphoto')).toBe(true)
    expect(slot.classList.contains('as-dphoto-empty')).toBe(true)
    expect(container.querySelector('.as-dhead .as-thumb')).toBeNull()
    const picker = container.parentElement!.querySelector(
      'input[type="file"][accept="image/*"]',
    ) as HTMLInputElement
    const click = vi.spyOn(picker, 'click')
    fireEvent.click(slot)
    expect(click).toHaveBeenCalledTimes(1)
    const file = new File(['png'], 'watch.png', { type: 'image/png' })
    fireEvent.change(picker, { target: { files: [file] } })
    expect(onPhoto).toHaveBeenCalledWith('oa_test000001', file)
  })
})

describe('AssetDetail — the header beside a photo', () => {
  it('puts the name, description and figures beside the photo on desktop', () => {
    const { container } = renderPage({ description: 'Ref 126231, Rolesor' })
    const head = container.querySelector('.as-dhead') as HTMLElement
    expect(head.classList.contains('as-dhead-media')).toBe(true)
    expect(head.querySelector('.as-dhead-title .as-dstats')).toBeTruthy()
    expect(container.querySelectorAll('.as-dstats')).toHaveLength(1)
    const sub = head.querySelector('.as-dhead-sub') as HTMLElement
    expect(sub.getAttribute('title')).toBe('Ref 126231, Rolesor')
  })

  it('lays the page out the same with no photo — the figures beside its place', () => {
    const { container } = renderPage({ photo_ref: null })
    expect(container.querySelector('.as-dhead-media')).toBeTruthy()
    expect(container.querySelector('.as-dhead-title .as-dstats')).toBeTruthy()
    expect(container.querySelectorAll('.as-dstats')).toHaveLength(1)
    expect(container.querySelector('.as-dpage')).toBeTruthy()
  })
})

describe('AssetDetail — the figures', () => {
  it('shows a zero gain as plain S$0 — neither a green "+S$0" nor a loss', () => {
    // Value equals cost: the gain is exactly 0.
    const { container } = renderPage({ cost: 478808, value: 478808, marks: [] })
    const unrealized = Array.from(container.querySelectorAll('.as-dstat-v'))[1]!
    expect(unrealized.textContent).toBe('S$0')
    expect(unrealized.classList.contains('pos')).toBe(false)
    expect(unrealized.classList.contains('neg')).toBe(false)
  })

  it('draws Value, Net equity and Unrealized at one size — mixed sizes read as messy', () => {
    const { container } = renderPage()
    expect(container.querySelectorAll('.as-dstat-v.sm')).toHaveLength(0)
    const css = readFileSync(resolve(__dirname, './assets.css'), 'utf8')
    expect(css).not.toMatch(/\.as-dstat-v\.sm\b/)
  })
})
