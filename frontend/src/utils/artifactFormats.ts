import type { ArtifactRef, ArtifactType } from '../types/chat'

// Per-format facts for chat artifacts. html/svg are DOCUMENTS rendered in the sandboxed iframe
// (utils/artifactFrame.ts); pdf/xlsx are FILES the UI never fetches as text (the raw route
// answers 415 for them); csv is a file too, but its text is small enough to preview as a table.

export type MarkupArtifactType = Extract<ArtifactType, 'html' | 'svg'>

export function isMarkupType(type: ArtifactType): type is MarkupArtifactType {
  return type === 'html' || type === 'svg'
}

export type MarkupArtifact = ArtifactRef & { artifactType: MarkupArtifactType }

export function isMarkupArtifact(a: ArtifactRef): a is MarkupArtifact {
  return isMarkupType(a.artifactType)
}

/** pdf/xlsx — no raw text route, no iframe: only a download (and, for PDF, an open). */
export function isBinaryType(type: ArtifactType): boolean {
  return type === 'pdf' || type === 'xlsx'
}

const EXT_LABEL: Record<ArtifactType, string> = {
  html: 'HTML',
  svg: 'SVG',
  pdf: 'PDF',
  xlsx: 'XLSX',
  csv: 'CSV',
}

/** The extension a save names ("Save as PDF"). */
export function extLabel(type: ArtifactType): string {
  return EXT_LABEL[type]
}

const TYPE_LINE: Record<ArtifactType, string> = {
  html: 'Document • HTML',
  svg: 'Image • SVG',
  pdf: 'Document • PDF',
  xlsx: 'Spreadsheet • Excel',
  csv: 'Table • CSV',
}

export function typeLine(a: Pick<ArtifactRef, 'artifactType'>): string {
  return TYPE_LINE[a.artifactType]
}

export const CSV_PREVIEW_ROWS = 200

/** RFC 4180 CSV → rows of cells: quoted fields may hold commas, doubled quotes and newlines.
 * Pure text in, strings out — callers render cells as React text, never HTML. */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let cell = ''
  let quoted = false
  let i = text.charCodeAt(0) === 0xfeff ? 1 : 0
  for (; i < text.length; i++) {
    const ch = text[i]
    if (quoted) {
      if (ch !== '"') cell += ch
      else if (text[i + 1] === '"') {
        cell += '"'
        i++
      } else quoted = false
      continue
    }
    if (ch === '"' && cell === '') quoted = true
    else if (ch === ',') {
      row.push(cell)
      cell = ''
    } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && text[i + 1] === '\n') i++
      row.push(cell)
      rows.push(row)
      row = []
      cell = ''
    } else cell += ch
  }
  if (cell !== '' || row.length > 0) {
    row.push(cell)
    rows.push(row)
  }
  return rows
}
