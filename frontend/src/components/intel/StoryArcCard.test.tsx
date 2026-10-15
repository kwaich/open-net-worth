// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import type { ReactNode } from 'react'
import { StoryArcCard } from './StoryArcCard'
import type { StoryArc } from './storyMeta'
import { AssetNavProvider } from '../../state/assetNav'

// AssetLink (used by StoryArcCard) calls authFetch for ticker-only resolve; the F11 "show
// all history" fetch (GET /api/intel/signals/{id}) below reuses the same mock.
vi.mock('../../api/client', () => ({
  authFetch: vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ instrument_id: 'x', ticker: 'ASML' }),
  }),
}))
import { authFetch } from '../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>
function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}
const ASSET_LINK_RESOLVE = okJson({ instrument_id: 'x', ticker: 'ASML' })

// AssetNavProvider uses useNavigate + useRouterState internally — mock both.
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => ({ matches: [] }),
}))

// Wrap every render in AssetNavProvider so AssetLink can call useAssetNav().
function Wrapper({ children }: { children: ReactNode }) {
  return <AssetNavProvider>{children}</AssetNavProvider>
}

const STORY: StoryArc = {
  id: 's-asml',
  type: 'risk',
  label: 'ASML Holding',
  ticker: 'ASML',
  status: 'action_needed',
  summary: 'Fresh reporting suggests deeper tooling restrictions.',
  portfolio_impact: 'Directional risk to your S$23.4k position.',
  action_items: ['Hold — thesis intact', 'Set a price alert 8% below cost'],
  timeline: [
    {
      id: 't1',
      date: '2h ago',
      title: 'New China export-control headlines',
      summary: '',
      sources: [{ title: 'Reuters report', url: 'https://reuters.com/x', domain: 'reuters.com' }],
    },
  ],
  created_at: '',
  // Fresh: an action-needed story older than a week reads "Needs recheck" (signalQueue).
  updated_at: new Date().toISOString(),
}

function noop() {}

afterEach(cleanup)

describe('StoryArcCard', () => {
  it('uses the existing status signals without a full-height colored rail', () => {
    const { container } = render(<StoryArcCard story={STORY} onAskLucius={noop} />, {
      wrapper: Wrapper,
    })
    expect(container.querySelector('.intel-story-stripe')).toBeNull()
    expect(container.querySelector('.intel-story-unread')).toBeTruthy()
    expect(container.querySelector('.intel-story-status-dot')).toBeTruthy()
  })

  it('renders the headline (latest development), summary, type and status', () => {
    const { container, getByText, getAllByText } = render(
      <StoryArcCard story={STORY} onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    // headline appears in the card head (and again in the expanded timeline)
    expect(getAllByText('New China export-control headlines').length).toBeGreaterThan(0)
    expect(container.querySelector('.intel-story-headline')?.textContent).toContain(
      'New China export-control headlines',
    )
    expect(getByText(/Fresh reporting suggests/)).toBeTruthy()
    expect(getByText('Risk')).toBeTruthy()
    expect(getByText('Needs attention')).toBeTruthy()
    expect(getByText('ASML')).toBeTruthy()
  })

  it('shows an always-visible source citation', () => {
    const { container, getByText } = render(<StoryArcCard story={STORY} onAskLucius={noop} />, {
      wrapper: Wrapper,
    })
    expect(getByText('Sources')).toBeTruthy()
    const sourcesRow = container.querySelector('.intel-story-sources')
    expect(sourcesRow?.textContent).toContain('reuters.com')
  })

  it('says what the story means for you', () => {
    const { getByText } = render(
      <StoryArcCard
        story={{ ...STORY, portfolio_impact: 'New sector for you — no Technology exposure today' }}
        defaultExpanded
        onAskLucius={noop}
      />,
      { wrapper: Wrapper },
    )
    expect(getByText('For you').closest('.intel-story-impact')?.textContent).toContain(
      'New sector for you',
    )
  })

  it('fires onAskLucius from the footer', () => {
    const onAskLucius = vi.fn()
    const { getByText } = render(<StoryArcCard story={STORY} onAskLucius={onAskLucius} />, {
      wrapper: Wrapper,
    })
    fireEvent.click(getByText(/Ask Lucius/))
    expect(onAskLucius).toHaveBeenCalled()
  })

  it('renders action items', () => {
    const { getByText } = render(<StoryArcCard story={STORY} onAskLucius={noop} />, {
      wrapper: Wrapper,
    })
    expect(getByText('Hold — thesis intact')).toBeTruthy()
    expect(getByText('Set a price alert 8% below cost')).toBeTruthy()
  })
})

// INT15 regression: a raw ISO timeline entry (with `title: "Analysis"`, `summary: <the real why>`
// — the shape the backend used to write on EVERY sweep tick before the fix) must render a
// human-readable date and the actual summary content, never the bare ISO string.
describe('StoryArcCard timeline formatting (INT15)', () => {
  afterEach(cleanup)

  const RAW_ISO = '2026-07-17T20:16:48.256429+00:00'
  const STORY_WITH_ISO_TIMELINE: StoryArc = {
    ...STORY,
    timeline: [
      {
        id: 't-iso',
        date: RAW_ISO,
        title: 'Update',
        summary: 'ONWAI shares reversed after the EU regulator signaled a delay.',
        sources: [],
      },
    ],
  }

  it('formats the raw ISO date instead of rendering it verbatim, and shows the summary text', () => {
    const { container, getByText, queryByText } = render(
      <StoryArcCard story={STORY_WITH_ISO_TIMELINE} defaultExpanded onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    // The raw ISO timestamp must never appear as visible text.
    expect(queryByText(RAW_ISO)).toBeNull()
    // The real "why" content (summary) is what renders as the entry body.
    expect(
      getByText(/ONWAI shares reversed after the EU regulator signaled a delay\./),
    ).toBeTruthy()
    // The meaningful title ("Update") renders as a heading alongside the summary.
    const timeEl = container.querySelector('.intel-story-tl-time')
    expect(timeEl?.textContent).toBeTruthy()
    expect(timeEl?.textContent).not.toContain('T20:16:48')
  })

  it('never renders the old hardcoded "Analysis" placeholder title', () => {
    const { container } = render(
      <StoryArcCard story={STORY_WITH_ISO_TIMELINE} defaultExpanded onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    const tl = container.querySelector('.intel-story-timeline')
    expect(tl?.textContent).not.toContain('Analysis')
  })
})

describe('StoryArcCard lifecycle', () => {
  afterEach(cleanup)

  it('shows the unread dot and Read/Dismiss controls when unread', () => {
    const onRead = vi.fn()
    const onDismiss = vi.fn()
    const { container, getByText } = render(
      <StoryArcCard
        story={STORY}
        read={false}
        onRead={onRead}
        onDismiss={onDismiss}
        onAskLucius={noop}
      />,
      { wrapper: Wrapper },
    )
    expect(container.querySelector('.intel-story-unread')).toBeTruthy()
    fireEvent.click(getByText('Read'))
    expect(onRead).toHaveBeenCalledWith('s-asml')
    fireEvent.click(getByText('Dismiss'))
    expect(onDismiss).toHaveBeenCalledWith('s-asml')
  })

  it('drops the unread dot and the Read control once read', () => {
    const { container, queryByText } = render(
      <StoryArcCard story={STORY} read onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    expect(container.querySelector('.intel-story-unread')).toBeNull()
    expect(queryByText('Read')).toBeNull()
    expect(container.querySelector('.intel-story--read')).toBeTruthy()
  })

  it('marks read on Ask Lucius', () => {
    const onRead = vi.fn()
    const { getByText } = render(
      <StoryArcCard story={STORY} onRead={onRead} onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    fireEvent.click(getByText(/Ask Lucius/))
    expect(onRead).toHaveBeenCalledWith('s-asml')
  })

  it('renders the resolved banner and Reopen + Delete in the archive', () => {
    const onRestore = vi.fn()
    const onDelete = vi.fn()
    const { container, getByText } = render(
      <StoryArcCard
        story={STORY}
        resolved
        resolvedReason="Risk condition cleared."
        onRestore={onRestore}
        onDelete={onDelete}
        onAskLucius={noop}
      />,
      { wrapper: Wrapper },
    )
    expect(getByText('Resolved')).toBeTruthy()
    expect(container.querySelector('.intel-story-resolved-banner')?.textContent).toContain(
      'Risk condition cleared.',
    )
    fireEvent.click(getByText('Reopen'))
    expect(onRestore).toHaveBeenCalledWith('s-asml')
    fireEvent.click(getByText('Delete'))
    expect(onDelete).toHaveBeenCalledWith('s-asml')
    // archived cards never show the unread dot
    expect(container.querySelector('.intel-story-unread')).toBeNull()
  })

  it('shows Restore (not Reopen) for a dismissed signal', () => {
    const { getByText, queryByText } = render(
      <StoryArcCard story={STORY} dismissed onRestore={noop} onDelete={noop} onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    expect(getByText('Restore')).toBeTruthy()
    expect(queryByText('Reopen')).toBeNull()
  })
})

// ADR 2026-08-28-sourced-and-corroborated-intel-signal-updates: a timeline entry can carry
// `status: "flagged"` (an uncorroborated reversal) or `superseded: true` (a later, corroborated
// reversal invalidated it) — the UI must distinguish these from a plain confirmed entry.
describe('StoryArcCard timeline corroboration markers', () => {
  afterEach(cleanup)

  it('shows an Unconfirmed marker with an explanatory title on a flagged entry', () => {
    const story: StoryArc = {
      ...STORY,
      timeline: [
        {
          id: 't-flagged',
          date: '2h ago',
          title: 'Update',
          summary: 'Uber bid for Kestrel Dash reportedly rejected.',
          sources: [],
          status: 'flagged',
        },
      ],
    }
    const { container, getByText } = render(
      <StoryArcCard story={story} defaultExpanded onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    const marker = getByText('Unconfirmed')
    expect(marker).toBeTruthy()
    expect(marker.getAttribute('title')).toMatch(/second independent source/i)
    expect(container.querySelector('.intel-story-tl-event--superseded')).toBeNull()
  })

  it('shows a muted, struck-through Superseded marker on a superseded entry', () => {
    const story: StoryArc = {
      ...STORY,
      timeline: [
        {
          id: 't-superseded',
          date: '4h ago',
          title: 'Update',
          summary: 'Morningstar confirms formal takeover agreement.',
          sources: [],
          superseded: true,
        },
      ],
    }
    const { container, getByText } = render(
      <StoryArcCard story={story} defaultExpanded onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    const marker = getByText('Superseded')
    expect(marker).toBeTruthy()
    const event = container.querySelector('.intel-story-tl-event--superseded')
    expect(event).toBeTruthy()
    expect(event?.textContent).toContain('Morningstar confirms formal takeover agreement.')
    expect(container.querySelector('.intel-story-tl-flag')).toBeTruthy()
  })

  it('renders a normal (confirmed) entry with no markers at all, unchanged', () => {
    const { container, queryByText } = render(
      <StoryArcCard story={STORY} defaultExpanded onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    expect(queryByText('Unconfirmed')).toBeNull()
    expect(queryByText('Superseded')).toBeNull()
    expect(container.querySelector('.intel-story-tl-flags')).toBeNull()
    expect(container.querySelector('.intel-story-tl-event--superseded')).toBeNull()
  })
})

// F11 fix (owner-reported, 2026-08-29): the feed's GET /api/intel/signals now embeds only a
// signal's most recent entries (storage itself is unbounded — see backend/services/
// intel_signals.py), reporting showing_timeline/total_timeline. This card must offer a way to
// fetch the rest, on demand, once expanded.
describe('StoryArcCard F11 — bounded feed timeline, fetch the rest on demand', () => {
  afterEach(() => {
    cleanup()
    mockAuthFetch.mockReset()
    mockAuthFetch.mockResolvedValue(ASSET_LINK_RESOLVE)
  })

  const BOUNDED_STORY: StoryArc = {
    ...STORY,
    timeline: [
      {
        id: 't-recent',
        date: '2h ago',
        title: 'Update',
        summary: 'Recent development.',
        sources: [],
      },
    ],
    showing_timeline: 1,
    total_timeline: 5,
  }

  it('shows a "Show N earlier" control when the feed embedded fewer entries than exist', () => {
    const { getByText } = render(
      <StoryArcCard story={BOUNDED_STORY} defaultExpanded onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    expect(getByText('Show 4 earlier')).toBeTruthy()
  })

  it('does not show the control when the story is not actually bounded', () => {
    const { queryByText } = render(
      <StoryArcCard story={STORY} defaultExpanded onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    expect(queryByText(/Show .* earlier/)).toBeNull()
  })

  it('fetches the single-signal endpoint and reveals the full timeline on click', async () => {
    const fullTimeline = [
      {
        id: 't-recent',
        date: '2h ago',
        title: 'Update',
        summary: 'Recent development.',
        sources: [],
      },
      {
        id: 't-old-1',
        date: '30d ago',
        title: 'Update',
        summary: 'An older development.',
        sources: [],
      },
    ]
    mockAuthFetch.mockImplementation((url: unknown) => {
      if (url === '/api/intel/signals/s-asml') {
        return Promise.resolve(okJson({ ...BOUNDED_STORY, timeline: fullTimeline }))
      }
      return Promise.resolve(ASSET_LINK_RESOLVE)
    })

    const { getByText, queryByText, findByText } = render(
      <StoryArcCard story={BOUNDED_STORY} defaultExpanded onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    fireEvent.click(getByText('Show 4 earlier'))
    expect(await findByText('An older development.')).toBeTruthy()
    // The control disappears once the full history has been loaded.
    expect(queryByText(/Show .* earlier/)).toBeNull()
  })

  it('keeps showing the bounded timeline if the full fetch fails', async () => {
    mockAuthFetch.mockImplementation((url: unknown) => {
      if (url === '/api/intel/signals/s-asml') {
        return Promise.resolve({ ok: false, json: async () => ({}) } as Response)
      }
      return Promise.resolve(ASSET_LINK_RESOLVE)
    })

    const { getByText, findByText } = render(
      <StoryArcCard story={BOUNDED_STORY} defaultExpanded onAskLucius={noop} />,
      { wrapper: Wrapper },
    )
    fireEvent.click(getByText('Show 4 earlier'))
    // The control returns to its idle label rather than hanging on "Loading…" forever, and
    // the (still-bounded) recent entry is still visible — no crash, no blank card.
    expect(await findByText('Show 4 earlier')).toBeTruthy()
    expect(getByText('Recent development.')).toBeTruthy()
  })
  describe('explained watch alert', () => {
    it('shows what happened, why (with its sources), what it means for you and what to weigh', () => {
      const { getByText, container } = render(
        <StoryArcCard
          story={{
            ...STORY,
            explained: {
              trigger: 'company_move',
              what_happened: 'AMD fell 9.0%, about 11× its usual daily move.',
              why: 'Reports say the MI400 slipped a quarter.',
              what_it_means: 'That puts it inside your buy zone.',
              worth_weighing: ['Whether the delay changes the roadmap'],
              sources: [
                { title: 'MI400 delay', url: 'https://example.com/a', domain: 'example.com' },
              ],
              at: '2026-09-24T15:00:00Z',
            },
          }}
          defaultExpanded
          onAskLucius={noop}
        />,
        { wrapper: Wrapper },
      )
      expect(getByText('AMD fell 9.0%, about 11× its usual daily move.')).toBeTruthy()
      expect(getByText('What it means for you')).toBeTruthy()
      expect(getByText('Whether the delay changes the roadmap')).toBeTruthy()
      const src = container.querySelector('.intel-explained-src') as HTMLAnchorElement
      expect(src.textContent).toBe('example.com')
      expect(src.getAttribute('href')).toBe('https://example.com/a')
      expect(container.querySelector('.intel-opinion')).toBeNull() // information level
    })

    it('labels a lean and a suggestion as Lucius’s view', () => {
      const { getByText } = render(
        <StoryArcCard
          story={{
            ...STORY,
            explained: {
              trigger: 'company_move',
              what_happened: 'AMD fell 9.0%.',
              why: 'w',
              what_it_means: 'm',
              worth_weighing: [],
              sources: [],
              at: '',
              lean: 'bearish',
              lean_reason: 'The delay hits the thesis.',
              suggestion: 'Consider waiting for your buy zone.',
            },
          }}
          defaultExpanded
          onAskLucius={noop}
        />,
        { wrapper: Wrapper },
      )
      expect(getByText('Leans bearish')).toBeTruthy()
      expect(getByText('Consider waiting for your buy zone.')).toBeTruthy()
      expect(getByText(/not financial advice/)).toBeTruthy()
    })
  })

  describe('Lucius’s read', () => {
    const read = {
      text: 'Earnings in a week put the thesis to the test.',
      watch_for: ['Guidance on data-centre demand'],
      sources: [],
      at: '2026-09-24T15:00:00Z',
    }

    it('adds the read under the summary of a story the explained alert doesn’t cover', () => {
      const { getByText } = render(
        <StoryArcCard story={{ ...STORY, lucius_read: read }} defaultExpanded onAskLucius={noop} />,
        { wrapper: Wrapper },
      )
      expect(getByText('Lucius’s read')).toBeTruthy()
      expect(getByText('Earnings in a week put the thesis to the test.')).toBeTruthy()
      expect(getByText('Guidance on data-centre demand')).toBeTruthy()
    })

    it('gives way to the explained alert when both exist', () => {
      const { queryByText } = render(
        <StoryArcCard
          story={{
            ...STORY,
            lucius_read: read,
            explained: {
              trigger: 'company_move',
              what_happened: 'AMD fell 9.0%.',
              why: 'w',
              what_it_means: 'm',
              worth_weighing: [],
              sources: [],
              at: '',
            },
          }}
          defaultExpanded
          onAskLucius={noop}
        />,
        { wrapper: Wrapper },
      )
      expect(queryByText('Lucius’s read')).toBeNull()
    })
  })
})
