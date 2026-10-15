// @vitest-environment jsdom
//
// While first run covers the app, the shell behind it must be unreachable: jsdom does not
// implement the focus algorithm, so this pins what the browser acts on — `inert` (no focus, no
// clicks, out of the accessibility tree) on every shell region, and none on first run itself.

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { behindFirstRunProps } from './behindFirstRun'

afterEach(cleanup)

function Fixture({ firstRunShowing }: { firstRunShowing: boolean }) {
  const behind = behindFirstRunProps(firstRunShowing)
  return (
    <div>
      <nav {...behind}>
        <button>Dashboard</button>
      </nav>
      <div {...behind}>
        <button>Ask Lucius</button>
      </div>
      {firstRunShowing && (
        <div className="firstrun-shell">
          <button>Continue</button>
        </div>
      )}
    </div>
  )
}

describe('behindFirstRunProps', () => {
  it('makes shell controls inert and hidden while first run shows, leaving first run live', () => {
    render(<Fixture firstRunShowing />)
    for (const name of ['Dashboard', 'Ask Lucius']) {
      const control = screen.getByText(name)
      expect(control.closest('[inert]')).not.toBeNull()
      expect(control.closest('[aria-hidden="true"]')).not.toBeNull()
    }
    expect(screen.getByText('Continue').closest('[inert]')).toBeNull()
    expect(screen.getByText('Continue').closest('[aria-hidden]')).toBeNull()
  })

  it('leaves the shell fully interactive once first run is gone', () => {
    render(<Fixture firstRunShowing={false} />)
    for (const name of ['Dashboard', 'Ask Lucius']) {
      expect(screen.getByText(name).closest('[inert]')).toBeNull()
      expect(screen.getByText(name).closest('[aria-hidden]')).toBeNull()
    }
  })
})

describe('RouterShell', () => {
  const source = readFileSync(resolve(__dirname, '../../RouterShell.tsx'), 'utf8')

  it('spreads the inert props on every region of the shell behind first run', () => {
    // Attribute order is free; the spread just has to sit on the same element.
    const onSameTag = (cls: string) =>
      new RegExp(
        `<(nav|div|main)\\s[^>]*?${cls}[^>]*?\\{\\.\\.\\.behindFirstRun\\}|<(nav|div|main)\\s+\\{\\.\\.\\.behindFirstRun\\}\\s+${cls}`,
      )
    expect(source).toMatch(onSameTag('className=\\{`sidebar\\$\\{'))
    expect(source).toMatch(onSameTag('className="app-main"'))
    expect(source).toMatch(onSameTag('className="sidebar peek"'))
    // The chat panel, version banner, desktop nav cluster and mobile tab bar are wrapped.
    expect(source.match(/<div \{\.\.\.behindFirstRunWrap\}>/g)?.length).toBeGreaterThanOrEqual(4)
  })

  it('never makes the first-run overlay itself inert', () => {
    const firstRun = source.slice(source.indexOf('<FirstRun'), source.indexOf('onAuthChanged'))
    expect(firstRun).not.toContain('behindFirstRun')
  })
})
