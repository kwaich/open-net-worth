// screenSnapshotWire.ts — pure leaf: the screen-snapshot view-model + wire shapes and the
// camelCase→snake_case serializer. No React, no @/state/@/api/@/hooks imports, so the lucius
// core modules (screenContract.ts) can build the wire payload without reaching into host state.
//
// The React context provider/hooks live in state/screenSnapshot.tsx, which re-exports these for
// backward compatibility — every existing importer keeps working unchanged.

// A lazily-fetchable pointer to deeper on-screen content (a news article, a chart's series,
// off-screen rows). Body is NOT carried — Lucius fetches it via get_screen_detail(id), or the user
// taps "+ attach". `icon`/`heavy` are UI-only and never sent to the backend.
export interface ScreenHandle {
  id: string
  type: string // news_story | price_series | table | ...
  label: string
  descriptor?: string
  source?: string // server-resolvable: an http(s) URL or "price_series:<instrument_id>:<period>"
  icon?: string // panel glyph (UI only)
}

// A drivable action the screen (or the off-screen catalog) advertises to Lucius.
// `route` is a SYMBOLIC key (ScreenCommandChip maps it to a real path) — never a URL.
export interface ScreenCommandWire {
  name: string
  description: string
  params: Array<{
    name: string
    type: string
    required: boolean
    enum: string[] | null
    minimum: number | null
    maximum: number | null
    items: string | null
  }>
  route: string
  live: boolean
}

export interface ScreenSnapshot {
  id: string // screen/route id, e.g. "asset-detail"
  title: string // e.g. "NVIDIA Corporation"
  viewState?: Record<string, unknown> // active tab / range / selection
  facts?: Record<string, string> // key derived figures (the "passed inline" list)
  visibleText?: string // bounded free-text fallback
  handles?: ScreenHandle[] // deep-content pointers (metadata only)
  commands?: ScreenCommandWire[] // drivable-action manifest (already wire-shaped)
  asOf?: string // ISO capture time
}

// The wire shape sent to POST /api/chat (snake_case, matches backend models/screen.py).
export interface ScreenSnapshotWire {
  id: string
  title: string
  view_state: Record<string, unknown>
  facts: Record<string, string>
  visible_text: string
  handles: Array<{
    id: string
    type: string
    label: string
    descriptor: string
    source: string | null
    pinned: boolean
  }>
  commands: ScreenCommandWire[]
  as_of: string | null
}

export function serializeSnapshot(s: ScreenSnapshot, pinned: string[] = []): ScreenSnapshotWire {
  const pinnedSet = new Set(pinned)
  return {
    id: s.id,
    title: s.title,
    view_state: s.viewState ?? {},
    facts: s.facts ?? {},
    visible_text: s.visibleText ?? '',
    handles: (s.handles ?? []).map((h) => ({
      id: h.id,
      type: h.type,
      label: h.label,
      descriptor: h.descriptor ?? '',
      source: h.source ?? null,
      // The user tapped "+ attach" → tell the backend to fetch this one now (vs leaving it for
      // the model to fetch on demand only if it decides it needs it).
      pinned: pinnedSet.has(h.id),
    })),
    commands: s.commands ?? [],
    as_of: s.asOf ?? null,
  }
}
