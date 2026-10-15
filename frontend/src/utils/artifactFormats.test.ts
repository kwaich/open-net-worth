import { describe, it, expect } from 'vitest'
import { extLabel, isBinaryType, isMarkupType, parseCsv, typeLine } from './artifactFormats'
import { filenameFromDisposition } from '../api/artifacts'

describe('parseCsv', () => {
  it('splits plain rows and tolerates CRLF and a missing trailing newline', () => {
    expect(parseCsv('a,b\r\n1,2\r\n3,4')).toEqual([
      ['a', 'b'],
      ['1', '2'],
      ['3', '4'],
    ])
  })

  it('keeps commas, escaped quotes and newlines inside quoted fields', () => {
    expect(parseCsv('name,note\n"Smith, J","said ""hi""\nthen left"\n')).toEqual([
      ['name', 'note'],
      ['Smith, J', 'said "hi"\nthen left'],
    ])
  })

  it('keeps empty cells and strips a BOM', () => {
    expect(parseCsv('﻿a,,c\n,,\n')).toEqual([
      ['a', '', 'c'],
      ['', '', ''],
    ])
  })

  it('returns no rows for empty text', () => {
    expect(parseCsv('')).toEqual([])
  })
})

describe('artifact format helpers', () => {
  it('names the extension and the type line per format', () => {
    expect(extLabel('xlsx')).toBe('XLSX')
    expect(typeLine({ artifactType: 'svg' })).toBe('Image • SVG')
  })

  it('classifies markup vs binary types', () => {
    expect(isMarkupType('html') && isMarkupType('svg')).toBe(true)
    expect(isMarkupType('csv')).toBe(false)
    expect(isBinaryType('pdf') && isBinaryType('xlsx')).toBe(true)
    expect(isBinaryType('csv')).toBe(false)
  })
})

describe('filenameFromDisposition', () => {
  it('reads quoted, bare and RFC 5987 names', () => {
    expect(filenameFromDisposition('attachment; filename="q3 report.pdf"')).toBe('q3 report.pdf')
    expect(filenameFromDisposition('attachment; filename=a.csv')).toBe('a.csv')
    expect(filenameFromDisposition("attachment; filename*=UTF-8''caf%C3%A9.xlsx")).toBe('café.xlsx')
    expect(filenameFromDisposition(null)).toBeNull()
  })
})
