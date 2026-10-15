// Adding an asset's documents: the Documents tab's Add file and a drop anywhere on the asset
// page share ONE upload, so its progress and its refusal show in the tab whichever started it.
import { useRef, useState, type DragEvent } from 'react'
import type { OwnedAsset } from '../../../types/assets'
import * as api from './api'

export interface DocUpload {
  saving: boolean
  /** Why a file wasn't added — its name, then the server's reason. */
  error: string | null
  addFiles(files: File[]): Promise<void>
}

/** Several files go up one by one; a refused one never stops the rest. */
export function useDocUpload(a: OwnedAsset, onToast: (message: string) => void): DocUpload {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const running = useRef(false)

  async function addFiles(files: File[]) {
    if (running.current || files.length === 0) return
    running.current = true
    setSaving(true)
    setError(null)
    let added = 0
    const refused: string[] = []
    for (const file of files) {
      try {
        await api.uploadDoc(a.id, file)
        added += 1
      } catch (e) {
        refused.push(`${file.name} wasn't added — ${e instanceof Error ? e.message : 'try again.'}`)
      }
    }
    running.current = false
    setSaving(false)
    if (refused.length > 1) setError(`${refused[0]} (and ${refused.length - 1} more)`)
    else if (refused.length === 1) setError(refused[0]!)
    if (added) onToast(added === 1 ? 'File added.' : `${added} files added.`)
  }

  return { saving, error, addFiles }
}

// Only files from the computer: Lucius's mention chips and dragged text carry no files.
const carriesFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files')

/** A region files can be dropped on — `over` while they hover it. */
export function useFileDrop(onFiles: (files: File[]) => void) {
  const [over, setOver] = useState(false)
  return {
    over,
    handlers: {
      onDragOver(e: DragEvent<HTMLElement>) {
        if (!carriesFiles(e)) return
        e.preventDefault()
        e.dataTransfer.dropEffect = 'copy'
        setOver(true)
      },
      onDragLeave(e: DragEvent<HTMLElement>) {
        // Moving between children fires leave on the one left behind — only leaving the
        // region itself ends it.
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false)
      },
      onDrop(e: DragEvent<HTMLElement>) {
        if (!carriesFiles(e)) return
        e.preventDefault()
        setOver(false)
        onFiles(Array.from(e.dataTransfer.files))
      },
    },
  }
}
