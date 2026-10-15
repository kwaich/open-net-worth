// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import * as React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import ts from 'typescript'
import type { DesktopBridge } from '../../desktop/src/contracts'
import * as platform from './platform/desktop'
import { DesktopStartupSplash } from './components/DesktopStartupSplash'

const source = ts.createSourceFile(
  'RouterShell.tsx',
  readFileSync(resolve(__dirname, 'RouterShell.tsx'), 'utf8'),
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
)
const desktopImport = source.statements.find(
  (node): node is ts.ImportDeclaration =>
    ts.isImportDeclaration(node) &&
    ts.isStringLiteral(node.moduleSpecifier) &&
    node.moduleSpecifier.text === './platform/desktop',
)
const named = desktopImport?.importClause?.namedBindings
const shell = source.statements.find(
  (node): node is ts.FunctionDeclaration =>
    ts.isFunctionDeclaration(node) && node.name?.text === 'RouterShellInner',
)
const boot = shell?.body?.statements.find(
  (node) =>
    ts.isIfStatement(node) &&
    node.expression.getText(source).includes('setupStatus.initialLoading'),
)
if (!named || !ts.isNamedImports(named) || !boot)
  throw new Error('RouterShell boot branch is missing')
const bindings = named.elements
  .map((binding) =>
    binding.propertyName ? `${binding.propertyName.text}: ${binding.name.text}` : binding.name.text,
  )
  .join(', ')
// Execute the actual first-paint branch with its actual platform imports, without mounting
// unrelated app subscriptions. A removed import must fail here just as it does in Electron.
const compiled = ts.transpileModule(`const { ${bindings} } = platform; ${boot.getText(source)}`, {
  compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022 },
}).outputText
const firstPaint = new Function(
  'platform',
  'React',
  'DesktopStartupSplash',
  'setupAnswered',
  'setupStatus',
  'firstRunDoneThisSession',
  compiled,
)

afterEach(() => {
  cleanup()
  delete window.onwDesktop
  delete window.onwDesktopClient
})

function renderFirstPaint() {
  return render(
    firstPaint(
      platform,
      React,
      DesktopStartupSplash,
      { current: false },
      { initialLoading: true },
      { current: false },
    ),
  )
}

it('renders the native local startup splash while setup status is still loading', () => {
  window.onwDesktop = { version: 1 } as DesktopBridge
  renderFirstPaint()
  expect(screen.getByRole('status', { name: 'Opening your book…' })).toBeTruthy()
})

it('renders server and ordinary browser boot without requiring local desktop capabilities', () => {
  const first = renderFirstPaint()
  expect(first.container.querySelector('.app-boot')).toBeTruthy()
  cleanup()
  window.onwDesktopClient = {
    version: 1,
    getConnection: async () => ({
      mode: 'server',
      serverUrl: 'https://book.example.test/',
      bookFolder: null,
    }),
    changeConnection: async () => {},
    openBrowser: async () => {},
    onCommand: () => () => {},
  }
  expect(renderFirstPaint().container.querySelector('.app-boot')).toBeTruthy()
})
