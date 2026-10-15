// Frontend mirror of the backend `ConnectorView` (backend/models/connector.py).
// The unified `/api/connectors` plane drives card identity, status and the Pause/Resume
// switch. Settings are written through the per-provider endpoints — never duplicate that
// config state into this view.

/** The catalog key of a connector (`email`, `plaid`, `web_search`, `mcp`, …). */
export type ConnectorKind = string

export type ConnectorStatus = 'not_configured' | 'configured' | 'active' | 'error' | string

/** One tool a connector contributes — PRESENTATIONAL metadata for the detail view.
 *  `name` is the exact registry tool id (Lucius-callable); `source` is `native` (static map)
 *  or `mcp` (live-advertised by an external MCP server). Display-only — no dispatch change. */
export interface ConnectorTool {
  name: string
  description: string
  source: 'native' | 'mcp' | string
  /** This tool's EFFECTIVE permission level, joined server-side from the permission table —
   *  always a real level, never absent-meaning-unset (an undecided tool reads
   *  `needs_approval`). Every connector family carries one; the group aggregate (`custom`)
   *  is derived from these, never sent. Set it via the `/api/connectors/{id}/permissions`
   *  routes — never by editing a tool. */
  level?: 'always_allow' | 'needs_approval' | 'blocked'
  /** Which ladder heading this tool is listed under — read-only vs. write & delete.
   *  LABELLING ONLY: it decides nothing about the level and nothing about whether a call fires. */
  read_only?: boolean
}

/** The unified read shape for one connector. NEVER carries a secret value. */
export interface Connector {
  id: string
  kind: ConnectorKind
  name: string
  enabled: boolean
  status: ConnectorStatus
  /** The redacted last error — present only while `status` is `error`. */
  error?: string | null
  /** False only for the price sources (Yahoo Finance, CoinGecko): always on, configured but
   *  never paused or removed. */
  pausable: boolean
  /** Public settings plus public state (last status, an MCP server's endpoint…). */
  config: Record<string, unknown>
  /** `has_<field>` presence flags — never a value. */
  secrets_public: Record<string, boolean>
  /** One-line subtitle, shown on the card + the detail header. */
  short_description?: string
  /** A paragraph, shown in the detail Modal. */
  long_description?: string
  /** The connector's tool bundle (native | mcp). Empty for transport-only connectors. */
  tools?: ConnectorTool[]
}
