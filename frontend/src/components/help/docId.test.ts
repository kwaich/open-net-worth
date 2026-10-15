import { describe, expect, it } from 'vitest'
import { docIdFromPathname, docIdToPath, isHelpPathname } from './docId'

describe('docIdToPath / docIdFromPathname', () => {
  it('round-trips a doc_id through its in-app path', () => {
    const docId = 'features/budgeting'
    expect(docIdToPath(docId)).toBe('/help/features/budgeting')
    expect(docIdFromPathname(docIdToPath(docId))).toBe(docId)
  })

  it('returns null (no doc selected) for the bare /help landing path', () => {
    expect(docIdFromPathname('/help')).toBeNull()
    expect(docIdFromPathname('/help/')).toBeNull()
  })

  it('returns null for a pathname outside /help entirely', () => {
    expect(docIdFromPathname('/overview')).toBeNull()
    expect(docIdFromPathname('/helper')).toBeNull()
  })

  it('trims a trailing slash on a doc path', () => {
    expect(docIdFromPathname('/help/features/budgeting/')).toBe('features/budgeting')
  })
})

describe('isHelpPathname', () => {
  it('is true for /help and every /help/* sub-path', () => {
    expect(isHelpPathname('/help')).toBe(true)
    expect(isHelpPathname('/help/features/budgeting')).toBe(true)
  })

  it('is false for anything else, including a merely-prefixed path', () => {
    expect(isHelpPathname('/overview')).toBe(false)
    expect(isHelpPathname('/helper')).toBe(false)
  })
})
