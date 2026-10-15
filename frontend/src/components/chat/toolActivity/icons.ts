// Verb class → icon. Monochrome line icons, never emoji.
//
// The classes are the closed set `backend/ai/tools/display.py` emits (its `DEFAULT_NOUNS`
// keys, plus the `error` class this UI adds for a failed call). A class arriving with no
// entry here is a real defect — a row with no icon — so the fallback is deliberate and
// visible rather than an empty box: `Wrench` reads as "some tool", which is exactly what an
// unrecognised class means.

import type { LucideIcon } from 'lucide-react'
import { Icons, type IconComponent } from '../../tabs/connectors/icons'
import {
  AlertTriangle,
  Camera,
  Globe,
  KeyRound,
  Pencil,
  Plus,
  Search,
  Send,
  Eye,
  Terminal,
  Trash2,
  Wrench,
} from '../../../ui/icons'

const BY_VERB: Record<string, LucideIcon> = {
  // Eye, not Table: the read class covers `net_worth`, `recall_facts` and
  // `get_screen_detail` as much as it covers row reads, and a table glyph claims tabular
  // data specifically (owner, 2026-08-22).
  read: Eye,
  search: Search,
  browse: Globe,
  create: Plus,
  update: Pencil,
  delete: Trash2,
  run: Terminal,
  capture: Camera,
  send: Send,
  auth: KeyRound,
  unknown: Wrench,
  error: AlertTriangle,
}

export function iconForVerb(verb: string): LucideIcon {
  return BY_VERB[verb] ?? Wrench
}

/**
 * The stand-in for a THIRD-PARTY MCP server with no brand mark of its own.
 *
 * Not decoration — provenance. Before labels were humanized, the raw name
 * (`mcp__playwright__browser_navigate`) announced its own origin, and that `mcp__` was the
 * only thing telling a person a third party ran this. Humanizing the label erased it, and
 * the UNVERIFIED fence is hidden from the chat UI by design (ADR 2026-08-22), so without
 * this glyph an external call is indistinguishable from one the app ships.
 *
 * A resolved brand mark still wins — a logo identifies the server far better than a generic
 * glyph, and a mark only resolves after an ENDPOINT match, which a server cannot fake.
 */
export const MCP_PLACEHOLDER = Icons.cpu

/** The Connectors screen's own category glyph for a connector with no brand mark, so the
 *  same connector looks like itself in both places. Falls back to nothing (the caller then
 *  uses the verb glyph) for a key this set does not carry. */
export function connectorIcon(key: string): IconComponent | undefined {
  return (Icons as Record<string, IconComponent | undefined>)[key]
}

/** The icon box side, in px. One number so the logo and the glyph occupy the same column —
 *  a rail whose items are different widths stops reading as a rail. */
export const ICON_SIZE = 14
