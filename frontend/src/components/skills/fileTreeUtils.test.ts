import { describe, expect, it } from 'vitest'
import { deriveFileTree, getBundleFileContent, isCodeOnlyFile } from './fileTreeUtils'
import type { SkillBundle } from '../../types/skills'

const EMPTY_BUNDLE: SkillBundle = {
  skill_md: '# Hello\nSome instructions.',
  manifest: {},
  files: {},
}

const SCRIPT_BUNDLE: SkillBundle = {
  skill_md: '# Monthly Close\nRun the close.',
  manifest: {
    scripts: [
      { id: 'main', entry: 'scripts/main.py' },
      { id: 'helper', entry: 'scripts/helper.py' },
    ],
  },
  files: {
    'scripts/main.py': 'print("hello")',
    'scripts/helper.py': 'def helper(): pass',
    'README.md': '# README',
  },
}

describe('deriveFileTree', () => {
  it('always prepends a synthetic SKILL.md node first', () => {
    const tree = deriveFileTree(EMPTY_BUNDLE)
    expect(tree[0]).toMatchObject({ name: 'SKILL.md', path: 'SKILL.md', isDir: false })
  })

  it('returns only SKILL.md for an empty bundle', () => {
    const tree = deriveFileTree(EMPTY_BUNDLE)
    expect(tree).toHaveLength(1)
    expect(tree[0]?.path).toBe('SKILL.md')
  })

  it('creates a scripts/ directory node with children', () => {
    const tree = deriveFileTree(SCRIPT_BUNDLE)
    // SKILL.md is first
    expect(tree[0]?.path).toBe('SKILL.md')
    // Find the scripts dir (dirs come before files)
    const scriptsDir = tree.find((n) => n.path === 'scripts' && n.isDir)
    expect(scriptsDir).toBeDefined()
    expect(scriptsDir!.children).toHaveLength(2)
    const childPaths = scriptsDir!.children!.map((c) => c.path)
    expect(childPaths).toContain('scripts/main.py')
    expect(childPaths).toContain('scripts/helper.py')
  })

  it('puts dirs before files at root level', () => {
    const tree = deriveFileTree(SCRIPT_BUNDLE)
    // After SKILL.md: scripts/ dir should come before README.md
    const nonSkillMdNodes = tree.slice(1)
    const firstDir = nonSkillMdNodes.findIndex((n) => n.isDir)
    const firstFile = nonSkillMdNodes.findIndex((n) => !n.isDir)
    if (firstDir !== -1 && firstFile !== -1) {
      expect(firstDir).toBeLessThan(firstFile)
    }
  })

  it('sorts children alphabetically within a directory', () => {
    const tree = deriveFileTree(SCRIPT_BUNDLE)
    const scriptsDir = tree.find((n) => n.path === 'scripts' && n.isDir)
    const childNames = scriptsDir!.children!.map((c) => c.name)
    expect(childNames).toEqual([...childNames].sort())
  })
})

describe('getBundleFileContent', () => {
  it('returns skill_md for SKILL.md path', () => {
    expect(getBundleFileContent(SCRIPT_BUNDLE, 'SKILL.md')).toBe(SCRIPT_BUNDLE.skill_md)
  })

  it('returns file source for a known path', () => {
    expect(getBundleFileContent(SCRIPT_BUNDLE, 'scripts/main.py')).toBe('print("hello")')
  })

  it('returns undefined for unknown path', () => {
    expect(getBundleFileContent(SCRIPT_BUNDLE, 'nonexistent.py')).toBeUndefined()
  })
})

describe('isCodeOnlyFile', () => {
  it('returns false for .md files', () => {
    expect(isCodeOnlyFile('SKILL.md')).toBe(false)
    expect(isCodeOnlyFile('README.md')).toBe(false)
  })

  it('returns true for .py files', () => {
    expect(isCodeOnlyFile('scripts/main.py')).toBe(true)
  })

  it('returns true for other non-markdown extensions', () => {
    expect(isCodeOnlyFile('scripts/utils.js')).toBe(true)
    expect(isCodeOnlyFile('config.json')).toBe(true)
  })
})
