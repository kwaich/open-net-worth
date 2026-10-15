import { useState } from 'react'
import { removeArtifactFromVault, saveArtifactToVault } from '../../api/vault'
import { refreshVault, useVault } from '../../hooks/useVault'
import { Bookmark, BookmarkCheck } from '../../ui/icons'

// "Save to Documents" on an artifact Lucius made — the user decides what outlives the chat. Saved
// state comes from the shared Vault listing (one request for every card on screen); a click
// flips it immediately and the listing catches up.
export function SaveToVaultButton({ artifactId }: { artifactId: string }) {
  const { data } = useVault({ staleTime: 30_000 })
  const listed = !!data?.items.some((i) => i.kind === 'artifact' && i.source_id === artifactId)
  // The click's result shows at once, but only until the listing moves: from then on the
  // listing is the truth again, so a change made elsewhere (the Documents panel, Lucius) shows here.
  const [optimistic, setOptimistic] = useState<{ value: boolean; basis: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const saved = optimistic && optimistic.basis === listed ? optimistic.value : listed

  const toggle = async () => {
    if (busy) return
    const next = !saved
    setBusy(true)
    setError(null)
    setOptimistic({ value: next, basis: listed })
    try {
      if (next) await saveArtifactToVault(artifactId)
      else await removeArtifactFromVault(artifactId)
      refreshVault()
    } catch (e) {
      setOptimistic(null)
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const label = error ? 'Try again' : saved ? 'Saved' : 'Save'
  const title = error ?? (saved ? 'In Documents — click to remove it' : 'Save to Documents')
  return (
    <button
      type="button"
      className={`artifact-chip-open artifact-chip-save${saved ? ' is-saved' : ''}`}
      onClick={() => void toggle()}
      aria-pressed={saved}
      aria-label={saved ? 'Saved to Documents' : 'Save to Documents'}
      title={title}
      disabled={busy}
    >
      {saved ? (
        <BookmarkCheck size={13} strokeWidth={1.8} />
      ) : (
        <Bookmark size={13} strokeWidth={1.6} />
      )}
      <span className="artifact-chip-label">{label}</span>
    </button>
  )
}
