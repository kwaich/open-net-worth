// @vitest-environment jsdom
//
// Connector credential fields must never reach the screen snapshot. They carry no id/name and
// no type="password", so isSecretControl never fires and harvestForm only skips them today
// because it cannot resolve a label at all — a routine a11y fix would start leaking MCP argv,
// URL and env. data-ctx-secret is the real control; the second test pins it against
// a11y-fixed markup, and the third proves that test has teeth.

import { describe, it, expect, afterEach } from 'vitest'
import { getScreenSnapshot } from '../../../lucius/screenContract'

const SECRET_ENV = 'API_KEY=sk-live-must-never-reach-the-model'
const SECRET_ARGS = '-y @modelcontextprotocol/server-postgres postgres://user:pw@host/db'
const SECRET_URL = 'https://mcp.example.com?api_key=sk-live-also-secret'

function mount(html: string) {
  document.body.innerHTML = `<div data-ctx-screen="Connectors">${html}</div>`
}

/** The connector field shape as it exists today: rd-label span + aria-labelledby, no id/name. */
function currentMarkup(secretWrapper: string): string {
  return `
    <div ${secretWrapper}>
      <div class="rd-field full">
        <span class="rd-label" id="mcp-url-label">Server URL</span>
        <input class="rd-input" aria-labelledby="mcp-url-label" value="${SECRET_URL}" />
      </div>
      <div class="rd-field full">
        <span class="rd-label" id="mcp-args-label">Arguments</span>
        <input class="rd-input" aria-labelledby="mcp-args-label" value="${SECRET_ARGS}" />
      </div>
      <div class="rd-field full">
        <span class="rd-label" id="mcp-env-label">Environment variables (optional)</span>
        <textarea class="rd-textarea" aria-labelledby="mcp-env-label">${SECRET_ENV}</textarea>
      </div>
    </div>`
}

/** The SAME fields after a routine a11y fix: real <label for>, real ids. */
function a11yFixedMarkup(secretWrapper: string): string {
  return `
    <div ${secretWrapper}>
      <div class="rd-field full">
        <label for="mcp-url">Server URL</label>
        <input id="mcp-url" class="rd-input" value="${SECRET_URL}" />
      </div>
      <div class="rd-field full">
        <label for="mcp-args">Arguments</label>
        <input id="mcp-args" class="rd-input" value="${SECRET_ARGS}" />
      </div>
      <div class="rd-field full">
        <label for="mcp-env">Environment variables (optional)</label>
        <textarea id="mcp-env" class="rd-textarea">${SECRET_ENV}</textarea>
      </div>
    </div>`
}

function snapshotText(): string {
  const { raw, title, facts } = getScreenSnapshot(new Set())
  return [raw, title, JSON.stringify(facts)].join('\n')
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('connectors credential fields never reach the screen snapshot', () => {
  it('does not harvest MCP url / argv / env from the markup as it exists today', () => {
    mount(currentMarkup('data-ctx-secret=""'))
    const text = snapshotText()
    expect(text).not.toContain(SECRET_ENV)
    expect(text).not.toContain(SECRET_ARGS)
    expect(text).not.toContain(SECRET_URL)
  })

  it('still does not harvest them once the fields get real labels (data-ctx-secret is why)', () => {
    mount(a11yFixedMarkup('data-ctx-secret=""'))
    const text = snapshotText()
    expect(text).not.toContain(SECRET_ENV)
    expect(text).not.toContain(SECRET_ARGS)
    expect(text).not.toContain(SECRET_URL)
  })

  it('leaks without the wrapper — pinning that data-ctx-secret is the actual control', () => {
    mount(a11yFixedMarkup(''))
    const text = snapshotText()
    expect(text).toContain(SECRET_ENV)
  })
})
