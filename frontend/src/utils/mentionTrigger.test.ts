import { describe, it, expect } from 'vitest'
import { detectAtToken, detectSlashToken } from './mentionTrigger'

// The composer inserts a non-breaking space after each chip; typing "@"/"/" right after a chip
// leaves " @" / " /" before the caret. These guard that nbsp counts as a word boundary
// (the original bug: the picker wouldn't open until you typed an extra real space).
const NBSP = String.fromCharCode(0xa0)

describe('detectAtToken', () => {
  it('opens after a non-breaking space (chip trailing separator)', () => {
    const value = `${NBSP}@`
    const r = detectAtToken(value, value.length)
    expect(r).not.toBeNull()
    expect(r!.atPos).toBe(1)
    expect(r!.query).toBe('')
  })

  it('detects a query typed after an nbsp', () => {
    const value = `${NBSP}@app`
    expect(detectAtToken(value, value.length)!.query).toBe('app')
  })

  it('still opens after a regular space', () => {
    expect(detectAtToken('hi @x', 5)!.query).toBe('x')
  })

  it('does not trigger mid-word (emails)', () => {
    expect(detectAtToken('foo@bar', 7)).toBeNull()
  })
})

describe('detectSlashToken', () => {
  it('opens after a non-breaking space (chip trailing separator)', () => {
    const value = `${NBSP}/budget`
    const r = detectSlashToken(value, value.length)
    expect(r).not.toBeNull()
    expect(r!.query).toBe('budget')
  })

  it('a non-breaking space closes the verb token (single word)', () => {
    // "/close x" — the nbsp stops the backward walk before reaching "/".
    const value = `/close${NBSP}x`
    expect(detectSlashToken(value, value.length)).toBeNull()
  })
})

describe('the @ query is bounded so the picker stops eating Enter', () => {
  it('still opens for a multi-word entity name', () => {
    const v = '@Meridian Bank Visa Infinite'
    expect(detectAtToken(v, v.length)).toEqual({ atPos: 0, query: 'Meridian Bank Visa Infinite' })
  })

  it('closes once the caret is clearly in prose', () => {
    // Owner-reported: an armed picker swallows the Enter you meant as "send".
    const v = '@Checking what did I spend last month'
    expect(detectAtToken(v, v.length)).toBeNull()
  })

  it('treats a run of spaces as one word gap, not several', () => {
    const v = '@whole   foods'
    expect(detectAtToken(v, v.length)).toEqual({ atPos: 0, query: 'whole   foods' })
  })

  it('still finds a mention typed mid-sentence', () => {
    const v = 'spending at @whole foods'
    expect(detectAtToken(v, v.length)).toEqual({ atPos: 12, query: 'whole foods' })
  })
})
