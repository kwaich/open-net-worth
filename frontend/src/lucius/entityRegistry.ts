// entityRegistry.ts — client-side entity descriptor registry.
//
// The backend's mention descriptors (backend/services/mentions/descriptors/) are the ONE
// source. The host seeds this registry synchronously before first render
// (seedEntityRegistry — ONW reads the copy the server embeds in index.html), and may
// refresh it from GET /api/entity-types. There is deliberately NO bundled copy: a
// build-time one drifted silently from the backend (ADR
// 2026-10-05-entity-types-ship-with-the-page-not-the-bundle).
//
// Extraction-readiness (§14.8 R-EX1): this module has NO ONW-domain imports
// (@/api, @/state, @/hooks). The refresh goes through the host's DataSourceAdapter
// (refreshFromAdapter), so ONW's authFetch stays at the app boundary.

export interface ClientFieldSpec {
  key: string
  label: string
  type: string
  sensitive: boolean
  showInPopover: boolean
}

export interface ClientDescriptor {
  type: string
  label: string
  icon: string
  tone: string
  fields: ClientFieldSpec[]
}

/** The /api/entity-types wire shape → the core ClientDescriptor contract.
 *  The wire carries the field schema under `field_schema` (backend/services/mentions/
 *  entity_types.py); the core calls it `fields`. Every seed path (the index.html embed, the
 *  refresh, the test fixture) maps through here, so filterByDescriptor/fieldsFor only ever
 *  see the canonical `fields` array. */
export function toClientDescriptors(raw: unknown): ClientDescriptor[] {
  if (!Array.isArray(raw)) return []
  return (raw as unknown[])
    .filter((d): d is Record<string, unknown> => typeof d === 'object' && d !== null)
    .map((d) => {
      const fs = Array.isArray(d['fields'])
        ? (d['fields'] as ClientFieldSpec[])
        : Array.isArray(d['field_schema'])
          ? (d['field_schema'] as ClientFieldSpec[])
          : []
      return {
        type: String(d['type'] ?? ''),
        label: String(d['label'] ?? d['type'] ?? ''),
        icon: String(d['icon'] ?? '◆'),
        tone: String(d['tone'] ?? 'neutral'),
        fields: fs,
      }
    })
}

// ── Registry ────────────────────────────────────────────────────────────────

// Map keyed by descriptor.type. Empty until the host seeds it.
const _registry = new Map<string, ClientDescriptor>()

/** Seed (or overlay) descriptors synchronously — call before first render.
 *  Later entries win on overlap, same as a refresh. */
export function seedEntityRegistry(descriptors: ClientDescriptor[]): void {
  for (const d of descriptors) {
    if (d && typeof d.type === 'string') _registry.set(d.type, d)
  }
}

// ── Hydration from /api/entity-types ────────────────────────────────────────

/**
 * Refresh the registry from a descriptors() callback (e.g. a DataSourceAdapter).
 *
 * The ONW app root calls this with the adapter's descriptors() method — the adapter
 * already handles auth, error handling and the wire→core shape mapping.
 *
 * Usage in main.tsx after wireDataSource(onwDataSource):
 *   refreshFromAdapter(onwDataSource.descriptors.bind(onwDataSource))
 *
 * Safe to call multiple times; server entries win over the seeded ones.
 * Network failures are silent — the seeded descriptors remain.
 */
export async function refreshFromAdapter(
  descriptorsFn: () => Promise<ClientDescriptor[]>,
): Promise<void> {
  try {
    seedEntityRegistry(await descriptorsFn())
  } catch {
    // Network failure or adapter error — seeded descriptors remain.
  }
}

// ── Accessors ────────────────────────────────────────────────────────────────

/** Full descriptor for a type, or undefined when none is registered. */
export function descriptorFor(type: string): ClientDescriptor | undefined {
  return _registry.get(type)
}

/** Entity type icon glyph. Falls back to '◆' for unknown types. */
export function iconFor(type: string): string {
  return _registry.get(type)?.icon ?? '◆'
}

/** Human-readable label for a type. Falls back to the raw type string. */
export function labelFor(type: string): string {
  return _registry.get(type)?.label ?? type
}

/** Tone token key for the type (e.g. "invest", "cash", "lucius", "neutral").
 *  Used with colorFor() as the `tone` field of a ColorItem. */
export function toneFor(type: string): string {
  return _registry.get(type)?.tone ?? 'neutral'
}

/** Field schema for a type (ordered insertion). Empty array for unknown types. */
export function fieldsFor(type: string): ClientDescriptor['fields'] {
  return _registry.get(type)?.fields ?? []
}

// ── Test-only helpers ─────────────────────────────────────────────────────────
// These are exported for unit tests only — do NOT call in production code.
// The __test__ prefix makes the intent clear.

/** Set or override a descriptor in the registry (test use only — not for runtime code). */
export function __test__setDescriptor(d: ClientDescriptor): void {
  _registry.set(d.type, d)
}

/** Remove a descriptor from the registry (test cleanup — restores the pre-test state). */
export function __test__deleteDescriptor(type: string): void {
  _registry.delete(type)
}

/** All registered descriptors in registry order. */
export function allDescriptors(): ClientDescriptor[] {
  return Array.from(_registry.values())
}

// ── filterByDescriptor ───────────────────────────────────────────────────────

/**
 * Filter an arbitrary field map to only the keys declared in the descriptor
 * schema for `type`, dropping keys absent from the schema AND keys marked
 * `sensitive: true` (defense-in-depth — server projection is the real boundary,
 * but we never stamp sensitive data into the DOM either).
 *
 * When `type` has no descriptor (unknown type), the input is returned unchanged
 * so that emitters for unknown types still emit something rather than nothing.
 *
 * Used by EntityRow to populate data-ctx-fields.
 */
export function filterByDescriptor(
  type: string,
  fields: Record<string, string | number | null | undefined>,
): Record<string, string | number | null | undefined> {
  const desc = _registry.get(type)
  // Defensive: a descriptor whose `fields` is absent or non-array (e.g. an adapter
  // that shipped a wire shape the core doesn't recognise) must NEVER crash render —
  // fall back to passing the fields through unfiltered rather than reading `.length`
  // off undefined.  (The host adapter is responsible for the canonical `fields` array;
  // this is the core's crash-proofing, per §14.3C "safe against an untrusted host".)
  if (!desc || !Array.isArray(desc.fields) || desc.fields.length === 0) return fields

  const allowed = new Map(desc.fields.map((f) => [f.key, f]))
  const out: Record<string, string | number | null | undefined> = {}
  for (const [k, v] of Object.entries(fields)) {
    const spec = allowed.get(k)
    if (!spec) continue // not in descriptor schema — drop
    if (spec.sensitive) continue // sensitive — always omit from DOM
    out[k] = v
  }
  return out
}
