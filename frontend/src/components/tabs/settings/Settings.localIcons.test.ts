import { readdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const settingsFile = resolve(__dirname, '../Settings.tsx')
const settingsCssFile = resolve(__dirname, 'settings.css')
const sectionsDir = resolve(__dirname, 'sections')
const indexHtmlFile = resolve(__dirname, '../../../../index.html')

const controlFiles = [
  settingsFile,
  resolve(__dirname, 'ds.tsx'),
  ...readdirSync(sectionsDir)
    .filter((name) => name.endsWith('Section.tsx'))
    .map((name) => resolve(sectionsDir, name)),
]

const controlSources = controlFiles.map((file) => readFileSync(file, 'utf8')).join('\n')

describe('Settings local icon contract', () => {
  it('does not depend on the remote Material Symbols ligature font', () => {
    expect(readFileSync(indexHtmlFile, 'utf8')).not.toContain('Material+Symbols')
    expect(readFileSync(settingsCssFile, 'utf8')).not.toContain('.msym')
    expect(controlSources).not.toMatch(/\bSym\b/)
  })

  it('routes ordinary control icons through the app-owned Lucide seam', () => {
    expect(controlSources).toContain('/ui/icons')
    expect(controlSources).not.toMatch(/from\s+['"]lucide-react['"]/)
    expect(controlSources).not.toMatch(/<svg\b/)
    expect(controlSources).not.toMatch(/>\s*✕\s*</)
    expect(readFileSync(settingsCssFile, 'utf8')).not.toContain('data:image/svg+xml')
    expect(controlSources.match(/<select\b/g) ?? []).toHaveLength(1)
  })

  it('keeps only the deliberate identity exception custom', () => {
    expect(readFileSync(settingsFile, 'utf8')).toContain("from '../../lucius/identity'")
  })
})
