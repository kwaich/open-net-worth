import type { VaultItem } from '../../api/vault'
import { VaultKindIcon } from './VaultList'

/** The preview's place for a file that has none (or can't be drawn): the same size as a
 *  preview, so every item's page has the same shape, saying to download it. */
export function NoPreview({ item, reason }: { item: VaultItem; reason?: string }) {
  return (
    <div className="vault-preview-none">
      <VaultKindIcon item={item} size={28} />
      <p className="vault-preview-none-title">No preview for this file</p>
      <p className="vault-preview-none-hint">{reason ? `${reason}.` : 'Download it to open it.'}</p>
    </div>
  )
}
