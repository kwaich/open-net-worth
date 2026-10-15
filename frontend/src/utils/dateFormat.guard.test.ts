import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, sep } from 'node:path'

// Settings → Date format only reaches a screen that writes its dates through `fDate` /
// `fMonth` / `fDateTime` (utils/format.ts). A hand-rolled `toLocaleDateString(…, { day, year })`
// silently ignores the preference and is how the register ended up in MM/DD/YYYY while the
// setting said otherwise. Month-only, weekday-only and clock-only labels are fine.

const SRC = join(__dirname, '..')

// Chart / venue-timezone code that needs an explicit `timeZone` (which `fDate` cannot take).
const ALLOWED = new Set([
  'utils/format.ts',
  'components/intel/assetPriceChartLwc.ts',
  'components/intel/AssetPriceChart.tsx',
  'components/intel/AssetDetailView.tsx',
  'components/intel/movementFormat.ts',
])

const CALL = /(toLocaleDateString|toLocaleString|DateTimeFormat)\(/g
const DATE_FIELD = /\b(day|year|dateStyle)\s*:/

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) {
      if (name !== 'node_modules') sourceFiles(full, out)
    } else if (/\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name)) out.push(full)
  }
  return out
}

/** The text between a call's parentheses (`start` is just past the opening one). */
function callArgs(text: string, start: number): string {
  let depth = 1
  for (let i = start; i < text.length; i++) {
    if (text[i] === '(') depth++
    else if (text[i] === ')' && --depth === 0) return text.slice(start, i)
  }
  return text.slice(start, start + 200)
}

describe('date display goes through fDate / fMonth / fDateTime', () => {
  it('no screen hand-formats a day or year with Intl', () => {
    const offenders: string[] = []
    for (const file of sourceFiles(SRC)) {
      const rel = relative(SRC, file).split(sep).join('/')
      if (ALLOWED.has(rel)) continue
      const text = readFileSync(file, 'utf8')
      for (const m of text.matchAll(CALL)) {
        if (DATE_FIELD.test(callArgs(text, m.index + m[0].length))) {
          offenders.push(`${rel}:${text.slice(0, m.index).split('\n').length}`)
        }
      }
    }
    expect(offenders, 'use fDate / fMonth / fDateTime from utils/format').toEqual([])
  })
})
