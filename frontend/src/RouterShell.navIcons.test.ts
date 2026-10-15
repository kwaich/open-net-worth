import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const shell = readFileSync(resolve(__dirname, 'RouterShell.tsx'), 'utf8')
const assetIcons = readFileSync(resolve(__dirname, 'components/tabs/assets/icons.tsx'), 'utf8')

describe('sidebar icon contract', () => {
  it('uses the shared Lucide Box icon for Assets', () => {
    expect(shell).toMatch(/\bBox,/)
    expect(shell).toContain('assets: <Box {...ICON_PROPS} />')
    expect(shell).not.toContain("from './components/tabs/assets/icons'")
  })

  it('does not retain the bespoke sidebar Assets icon', () => {
    expect(assetIcons).not.toContain('NavAssetsIcon')
  })
})

describe('sidebar footer feedback item', () => {
  it('sits beside Search and Hide amounts, with a bug icon, and opens the report dialog', () => {
    const footer = shell.slice(shell.indexOf('className="sidebar-util"'))
    expect(footer).toMatch(/onClick=\{\(\) => openReportProblem\(\)\}/)
    expect(footer).toContain('<Bug size={16} strokeWidth={1.6} aria-hidden="true" />')
    expect(footer).toContain('<span className="sidebar-label">Send feedback</span>')
    expect(footer).toMatch(/'Send feedback',\n\s*\)\}/)
  })
})
