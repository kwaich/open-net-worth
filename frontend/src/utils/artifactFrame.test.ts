import { describe, it, expect } from 'vitest'
import {
  artifactHeightFromMessage,
  prepareArtifactSrcDoc,
  sandboxFor,
  svgAspectRatio,
} from './artifactFrame'

// Security-critical invariants of the artifact srcDoc pipeline: the egress-blocking
// CSP must be the FIRST element of <head> (so it governs everything after it), any
// model-emitted CSP meta must be stripped, and SVG documents are wrapped in a page
// whose CSP carries NO script-src at all.

const OUR_CSP = /<meta http-equiv="Content-Security-Policy" content="default-src 'none';/

describe('sandboxFor', () => {
  it('allows scripts for html, but NEVER allow-same-origin', () => {
    const sandbox = sandboxFor('html')
    expect(sandbox).toBe('allow-scripts')
    expect(sandbox).not.toContain('allow-same-origin')
  })

  it('disables scripts entirely for svg', () => {
    expect(sandboxFor('svg')).toBe('')
  })
})

describe('prepareArtifactSrcDoc (html)', () => {
  it('injects the CSP as the first element of an existing <head>', () => {
    const out = prepareArtifactSrcDoc(
      '<!doctype html><html><head><title>t</title></head><body>hi</body></html>',
      'html',
    )
    const headAt = out.indexOf('<head>')
    expect(out.slice(headAt + '<head>'.length)).toMatch(OUR_CSP)
    expect(out.indexOf('content="default-src')).toBeLessThan(out.indexOf('<title>'))
  })

  it('creates a <head> right after <html> when the document has none', () => {
    const out = prepareArtifactSrcDoc('<html lang="en"><body>hi</body></html>', 'html')
    expect(out).toMatch(/<html lang="en"><head><meta http-equiv="Content-Security-Policy"/)
  })

  it('prepends the CSP to a bare fragment (lands in the implicit head)', () => {
    const out = prepareArtifactSrcDoc('<div>chart</div>', 'html')
    expect(out.startsWith('<meta http-equiv="Content-Security-Policy"')).toBe(true)
    expect(out).toContain('<div>chart</div>')
  })

  it('keeps a doctype FIRST on fragments so the frame stays in standards mode', () => {
    const out = prepareArtifactSrcDoc('<!DOCTYPE html><div>chart</div>', 'html')
    expect(out.startsWith('<!DOCTYPE html><meta http-equiv="Content-Security-Policy"')).toBe(true)
  })

  it('strips a model-emitted CSP meta so ours is the only policy', () => {
    const evil =
      '<html><head><meta http-equiv="Content-Security-Policy" content="default-src *">' +
      '<title>t</title></head><body></body></html>'
    const out = prepareArtifactSrcDoc(evil, 'html')
    expect(out).not.toContain('default-src *')
    expect(out.match(/Content-Security-Policy/g)).toHaveLength(1)
    expect(out).toMatch(OUR_CSP)
  })

  it('strips CSP metas regardless of quoting/case', () => {
    const evil = `<html><head><META HTTP-EQUIV='content-security-policy' CONTENT='img-src *'></head></html>`
    const out = prepareArtifactSrcDoc(evil, 'html')
    expect(out).not.toContain('img-src *')
  })

  it('blocks network egress but allows inline script/style for charts', () => {
    const out = prepareArtifactSrcDoc('<html><head></head></html>', 'html')
    expect(out).toContain("default-src 'none'")
    expect(out).toContain("script-src 'unsafe-inline'")
    expect(out).toContain("style-src 'unsafe-inline'")
    expect(out).not.toContain('http://')
    expect(out).not.toContain('https://')
  })
})

describe('height probe', () => {
  it('injects the probe script before </body> for html artifacts', () => {
    const out = prepareArtifactSrcDoc('<html><head></head><body>x</body></html>', 'html')
    expect(out).toContain('__onwArtifact')
    expect(out.indexOf('__onwArtifact')).toBeLessThan(out.indexOf('</body>'))
    expect(out.match(/__onwArtifact/g)!.length).toBeGreaterThanOrEqual(1)
  })

  it('appends the probe to fragments with no body tag', () => {
    const out = prepareArtifactSrcDoc('<div>chart</div>', 'html')
    expect(out).toContain('__onwArtifact')
  })

  it('does NOT inject the probe into svg artifacts (scripts are disabled there)', () => {
    const out = prepareArtifactSrcDoc('<svg viewBox="0 0 4 3"></svg>', 'svg')
    expect(out).not.toContain('__onwArtifact')
    expect(out).not.toContain('<script')
  })
})

describe('artifactHeightFromMessage', () => {
  it('accepts a well-formed probe report', () => {
    expect(artifactHeightFromMessage({ __onwArtifact: 'height', height: 480 })).toBe(480)
  })

  it('rejects malformed / hostile payloads', () => {
    expect(artifactHeightFromMessage(null)).toBeNull()
    expect(artifactHeightFromMessage('480')).toBeNull()
    expect(artifactHeightFromMessage({ height: 480 })).toBeNull()
    expect(artifactHeightFromMessage({ __onwArtifact: 'height', height: 'tall' })).toBeNull()
    expect(artifactHeightFromMessage({ __onwArtifact: 'height', height: -5 })).toBeNull()
    expect(artifactHeightFromMessage({ __onwArtifact: 'height', height: Infinity })).toBeNull()
  })
})

describe('svgAspectRatio', () => {
  it('derives the ratio from viewBox', () => {
    expect(svgAspectRatio('<svg viewBox="0 0 200 100"></svg>')).toBe(0.5)
  })

  it('falls back to width/height attributes', () => {
    expect(svgAspectRatio('<svg width="400" height="300"></svg>')).toBe(0.75)
  })

  it('returns null when no usable dimensions exist', () => {
    expect(svgAspectRatio('<svg></svg>')).toBeNull()
    expect(svgAspectRatio('not svg at all')).toBeNull()
  })
})

describe('prepareArtifactSrcDoc (svg)', () => {
  it('wraps the svg in a page whose CSP has NO script-src at all', () => {
    const out = prepareArtifactSrcDoc('<svg viewBox="0 0 10 10"></svg>', 'svg')
    expect(out).toContain('<svg viewBox="0 0 10 10"></svg>')
    expect(out).toContain("default-src 'none'")
    expect(out).not.toContain('script-src')
  })

  it('strips a CSP meta smuggled into svg content', () => {
    const out = prepareArtifactSrcDoc(
      '<meta http-equiv="Content-Security-Policy" content="default-src *"><svg></svg>',
      'svg',
    )
    expect(out).not.toContain('default-src *')
  })
})
