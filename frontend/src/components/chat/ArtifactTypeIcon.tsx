import type { ArtifactType } from '../../types/chat'
import { Code2, FileSpreadsheet, FileText, ImageIcon, Table } from '../../ui/icons'

const ICONS = {
  html: Code2,
  svg: ImageIcon,
  pdf: FileText,
  xlsx: FileSpreadsheet,
  csv: Table,
} as const

/** The glyph for an artifact's format — one mapping for the chip, the viewer and the vault. */
export function ArtifactTypeIcon({ type, size = 20 }: { type: ArtifactType; size?: number }) {
  const Icon = ICONS[type]
  return <Icon size={size} strokeWidth={1.4} />
}
