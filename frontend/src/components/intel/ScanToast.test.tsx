// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { ScanToast } from './ScanToast'

afterEach(cleanup)

it('shows a scan failure as an error rather than up to date', () => {
  const { getByText, queryByText } = render(
    <ScanToast scan={{ phase: 'error', msg: 'Scan failed. Try again.' }} onClose={() => {}} />,
  )
  expect(getByText('Scan failed')).toBeTruthy()
  expect(getByText('Scan failed. Try again.')).toBeTruthy()
  expect(queryByText('Up to date')).toBeNull()
})

it('shows a parked scan as processing rather than complete', () => {
  const { getByText, queryByText } = render(
    <ScanToast scan={{ phase: 'pending', msg: 'Results will appear later.' }} onClose={() => {}} />,
  )
  expect(getByText('Scan processing')).toBeTruthy()
  expect(queryByText('Up to date')).toBeNull()
})
