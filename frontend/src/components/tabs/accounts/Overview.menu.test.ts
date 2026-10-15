import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const overview = readFileSync(resolve(__dirname, 'Overview.tsx'), 'utf8')

describe('All-accounts overflow menu', () => {
  it('uses the shared menu icon treatment for every action', () => {
    expect(overview).toMatch(/from '\.\.\/\.\.\/\.\.\/ui\/icons'/)
    expect(overview).toMatch(/function OverviewMenuItem/)
    expect(overview).toMatch(/icon="payees"/)
    expect(overview).toMatch(/icon="categories"/)
    expect(overview).toMatch(/icon="import"/)
    expect(overview).toMatch(/icon="export"/)
    expect(overview).not.toMatch(/⤓ Export transactions/)
  })
})
