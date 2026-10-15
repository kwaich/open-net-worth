// @vitest-environment jsdom
//
// The rendered rail. The first test here IS the goal of this whole change, made executable:
// no raw tool name reaches the DOM on any chat surface.

import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ToolDisplay } from '../../../types/chat'
import { ToolActivity } from './ToolActivity'
import type { ActionItem, ToolItem } from './cluster'

afterEach(cleanup)

vi.mock('../../../utils/chatStore', () => ({ fetchToolDetail: vi.fn() }))

// The category-glyph fallback needs the connectors read (a kind's icon + hue are the
// server's to tell us). Mocked at the API layer so the real hook, cache and adapter all run.
vi.mock('../../../api/client', () => ({
  authFetch: vi.fn(async () => ({
    ok: true,
    json: async () => ({
      connectors: [
        {
          id: 'email',
          name: 'Email',
          kind: 'email',
          enabled: true,
          config: {},
          secrets_public: {},
          status: 'ok',
          pausable: true,
        },
      ],
    }),
  })),
  setUnauthorizedHandler: vi.fn(),
}))

let seq = 0
function display(over: Partial<ToolDisplay> = {}): ToolDisplay {
  return {
    label: 'Read transactions',
    verb: 'read',
    noun: 'transactions',
    owner_id: 'core',
    owner_name: '',
    detail: '',
    ...over,
  }
}
function tool(over: Partial<ToolItem> = {}): ToolItem {
  return {
    type: 'tool',
    name: 'get_transactions',
    id: `t${seq++}`,
    status: 'done',
    display: display(),
    input: { month: '2026-01' },
    result: '{"ok":true,"count":3,"missing":null}',
    ...over,
  }
}
const view = (items: ActionItem[]) => render(<ToolActivity items={items} sessionId="s1" />)

describe('the goal', () => {
  it('never renders a raw tool name', () => {
    const { container } = view([
      tool(),
      tool({
        name: 'mcp__playwright__browser_navigate',
        display: display({
          verb: 'browse',
          noun: 'pages',
          label: 'Opened vnexpress.vn',
          owner_id: 'mcp:playwright',
          owner_name: 'Playwright',
          detail: 'vnexpress.vn',
        }),
      }),
    ])
    const text = container.textContent ?? ''
    expect(text).not.toMatch(/mcp__/)
    expect(text).not.toMatch(/\b[a-z]+_[a-z_]+\b/)
    expect(text).toContain('Opened vnexpress.vn')
  })
})

describe('the summary line', () => {
  it('says what the group did, when that is more than any one row says', () => {
    const browse = display({ verb: 'browse', noun: 'pages', label: 'Open a page' })
    const { container } = view([tool(), tool(), tool({ display: browse })])
    expect(container.querySelector('.msg-toolact__summary')?.textContent).toContain(
      'Read 2 transactions, open pages',
    )
  })

  it('does the counting, so the rows do not have to', () => {
    // Rows are one-per-call since 2026-08-23. A counted row ("Update 2 run tasks") said what
    // the bar already said, and expanding it opened a nested list of the calls it stood for.
    const { container } = view([tool(), tool()])
    const rows = container.querySelectorAll('.msg-toolact__row')
    expect(rows).toHaveLength(2)
    expect([...rows].every((r) => !/\d/.test(r.textContent ?? ''))).toBe(true)
    expect(container.querySelector('.msg-toolact__summary')?.textContent).toContain(
      'Read 2 transactions',
    )
  })

  it('shows no bar above a lone call', () => {
    const { container } = view([tool()])
    expect(container.querySelectorAll('.msg-toolact__row')).toHaveLength(1)
    expect(container.querySelector('.msg-toolact__summary')).toBeNull()
  })

  it('reports the step count beside the phrase', () => {
    const browse = display({ verb: 'browse', noun: 'pages', label: 'Open a page' })
    const { container } = view([tool(), tool(), tool({ display: browse })])
    expect(container.querySelector('.msg-toolact__count')?.textContent).toBe('3 steps')
  })

  it('folds the whole group away, and back', () => {
    const browse = display({ verb: 'browse', noun: 'pages', label: 'Open a page' })
    const { container } = view([tool(), tool({ display: browse })])
    const bar = container.querySelector('.msg-toolact__summary') as HTMLElement
    const rail = container.querySelector('.msg-toolact__rail') as HTMLElement
    // Folded until someone opens it: the steps are the working, not the answer.
    expect(rail.hasAttribute('hidden')).toBe(true)
    fireEvent.click(bar)
    expect(rail.hasAttribute('hidden')).toBe(false)
    fireEvent.click(bar)
    // `hidden`, not unmounted — each row keeps its own open state across a fold.
    expect(rail.hasAttribute('hidden')).toBe(true)
  })

  it('is absent when there is nothing to summarize', () => {
    const { container } = render(<ToolActivity items={[]} sessionId="s1" />)
    expect(container.firstChild).toBeNull()
  })
})

describe('rows', () => {
  it('is collapsed by default and shows no request or result', () => {
    const { container } = view([tool()])
    expect(container.querySelector('.msg-toolact__detail')).toBeNull()
    expect(screen.queryByText('Request')).toBeNull()
  })

  it('expands on click into labelled Request and Result boxes', () => {
    view([tool()])
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText('Request')).toBeTruthy()
    expect(screen.getByText('Result')).toBeTruthy()
  })

  it('is a real button, so it is keyboard-reachable and announced', () => {
    view([tool()])
    const btn = screen.getByRole('button')
    expect(btn.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(btn)
    expect(screen.getByRole('button', { expanded: true })).toBeTruthy()
  })

  it('shows the connector breadcrumb, and none at all for an unscoped tool', () => {
    const { container } = view([
      tool({ display: display({ owner_id: 'ibkr', owner_name: 'Interactive Brokers' }) }),
    ])
    expect(container.querySelector('.msg-toolact__owner')?.textContent).toBe('Interactive Brokers')
    cleanup()
    const plain = view([tool()])
    expect(plain.container.querySelector('.msg-toolact__owner')).toBeNull()
  })

  it('gives a connector with no brand mark its OWN category glyph, not a verb glyph', async () => {
    // `email` and `webhook` are protocols, not brands, so they have no logo — and were
    // falling back to a generic verb glyph while the Connectors screen showed them their own
    // icon in their own colour (owner report, 2026-08-23). Each connector gets ITS own glyph;
    // a kind with none falls back to the generic one `adapter.PRESENTATION` already defines.
    const { container } = view([
      tool({ display: display({ owner_id: 'email', owner_name: 'Email' }) }),
    ])
    await waitFor(() =>
      expect(container.querySelector('.msg-toolact__icon--connector')).not.toBeNull(),
    )
    const icon = container.querySelector('.msg-toolact__icon--connector')
    expect(icon?.getAttribute('title')).toBe('Email')
    // Its OWN colour, not a shared one — `email` is `var(--recv)` on its Connectors card.
    expect((icon as HTMLElement).style.color).toBeTruthy()
  })

  it('shows a connector logo in place of the glyph when one resolves', () => {
    const { container } = view([
      tool({ display: display({ owner_id: 'ibkr', owner_name: 'Interactive Brokers' }) }),
    ])
    const img = container.querySelector('.msg-toolact__icon img') as HTMLImageElement | null
    expect(img?.getAttribute('src')).toContain('ibkr')
  })

  it('marks a third-party MCP server that has no brand mark of its own', () => {
    // Before labels were humanized, `mcp__…` in the raw name was the only thing telling a
    // person a third party ran the call. The label erased it and the UNVERIFIED fence is
    // hidden from the UI by design, so the glyph carries that provenance now.
    const { container } = view([
      tool({
        name: 'mcp__some-server__do_thing',
        display: display({ owner_id: 'mcp:some-server', owner_name: 'some-server' }),
      }),
    ])
    const icon = container.querySelector('.msg-toolact__icon--external')
    expect(icon).not.toBeNull()
    expect(icon?.getAttribute('title')).toBe('Third-party MCP server')
  })

  it('prefers a resolved brand mark over the stand-in', () => {
    // A mark only resolves after an ENDPOINT match, which a server cannot fake, so it
    // identifies the server better than a generic glyph ever could.
    const { container } = view([
      tool({ display: display({ owner_id: 'ibkr', owner_name: 'Interactive Brokers' }) }),
    ])
    expect(container.querySelector('.msg-toolact__icon img')).not.toBeNull()
    expect(container.querySelector('.msg-toolact__icon--external')).toBeNull()
  })

  it('does not mark a built-in connector as third-party', () => {
    const { container } = view([tool()])
    expect(container.querySelector('.msg-toolact__icon--external')).toBeNull()
  })

  it('a failure outranks the third-party stand-in', () => {
    const { container } = view([
      tool({
        name: 'mcp__some-server__do_thing',
        result: 'Error: nope',
        display: display({ owner_id: 'mcp:some-server', owner_name: 'some-server' }),
      }),
    ])
    expect(container.querySelector('.msg-toolact__icon--error')).not.toBeNull()
    expect(container.querySelector('.msg-toolact__icon--external')).toBeNull()
  })

  it('does not offer to expand a call that has nothing to show yet', () => {
    view([tool({ status: 'executing', input: undefined, result: undefined })])
    expect(screen.getByRole('button').hasAttribute('disabled')).toBe(true)
  })
})

describe('failures', () => {
  it('marks a plain-text MCP error as a failure, in the row AND the expanded box', () => {
    const { container } = view([
      tool({
        name: 'mcp__ibkr__create_alert',
        result: 'Error: Authentication required to create alerts.',
        display: display({
          verb: 'create',
          noun: 'alerts',
          label: 'Create alert',
          owner_id: 'mcp:ibkr',
          owner_name: 'IBKR',
        }),
      }),
    ])
    expect(container.querySelector('.msg-toolact__error')?.textContent).toBe(
      'Authentication required to create alerts.',
    )
    expect(container.querySelector('.msg-toolact__icon--error')).not.toBeNull()
    fireEvent.click(container.querySelector('.msg-toolact__head') as HTMLElement)
    expect(screen.getByText('Error')).toBeTruthy()
    expect(container.querySelector('.msg-toolact__code--error')).not.toBeNull()
  })

  it('states the error on the row, without needing a click', () => {
    view([tool({ result: '{"error":"account not found"}' })])
    expect(screen.getByText('account not found')).toBeTruthy()
  })

  it('reports the failure in the summary rather than absorbing it into a count', () => {
    const { container } = view([tool(), tool(), tool({ result: '{"error":"boom"}' })])
    expect(container.querySelector('.msg-toolact__summary')?.textContent).toContain('1 failed')
  })
})

describe('repeated calls', () => {
  it('renders one expandable row per call, with no nested level', () => {
    const { container } = view([tool(), tool(), tool()])
    // Folded by default now — open the group before reaching for its rows.
    fireEvent.click(container.querySelector('.msg-toolact__summary') as HTMLElement)
    const rows = container.querySelectorAll('.msg-toolact__row')
    expect(rows).toHaveLength(3)
    // The nested member list is gone with row-level clustering.
    expect(container.querySelector('.msg-toolact__members')).toBeNull()
    fireEvent.click(within(rows[1] as HTMLElement).getByRole('button'))
    expect(screen.getByText('Request')).toBeTruthy()
  })
})

describe('syntax highlighting', () => {
  it('emits the namespaced Prism token classes the stylesheet targets', () => {
    // Pins the `pr-` rewrite AND the exact token classes prism-json produces — chat.css
    // styles `pr-null` and `pr-operator`, which nothing else in the app references.
    const { container } = view([tool()])
    fireEvent.click(screen.getByRole('button'))
    // The RESULT box — the Request has no boolean or null to tokenize.
    const boxes = container.querySelectorAll('.msg-toolact__code')
    const html = boxes[boxes.length - 1]?.innerHTML ?? ''
    for (const cls of ['pr-property', 'pr-operator', 'pr-boolean', 'pr-null', 'pr-number']) {
      expect(html).toContain(cls)
    }
    // Nothing un-prefixed survives: Prism's raw `class="token punctuation"` is what
    // collided with unrelated app rules in the first place.
    expect(html).not.toMatch(/class="token\b/)
  })
})

describe('ordering', () => {
  it('keeps a page fetch in its place in the timeline', () => {
    const { container } = view([tool(), { type: 'fetch', url: 'https://a.test/x' }, tool()])
    const rows = [...container.querySelectorAll('.msg-toolact__row')].map(
      (r) => r.textContent ?? '',
    )
    expect(rows).toHaveLength(3)
    expect(rows[1]).toContain('a.test')
  })
})
