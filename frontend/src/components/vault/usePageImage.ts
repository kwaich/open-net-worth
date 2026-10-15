import { useEffect, useState } from 'react'
import { authFetch } from '../../api/client'

// One page of a PDF item as an image the server drew (backend/services/document_preview.py),
// with the document's page count — shared by the compact preview and the full-size reader.

interface Loaded {
  key: string
  url?: string
  count?: number
  error?: string
}

export function usePageImage(itemId: string, page: number): Loaded & { loading: boolean } {
  const key = `${itemId}:${page}`
  const [loaded, setLoaded] = useState<Loaded>({ key: '' })
  useEffect(() => {
    const controller = new AbortController()
    let url: string | undefined
    void (async () => {
      try {
        const res = await authFetch(
          `/api/vault/items/${encodeURIComponent(itemId)}/pages/${page}`,
          { signal: controller.signal },
        )
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as { detail?: unknown }
          const detail = typeof body.detail === 'string' ? body.detail : `HTTP ${res.status}`
          setLoaded({ key, error: detail })
          return
        }
        url = URL.createObjectURL(await res.blob())
        setLoaded({ key, url, count: Number(res.headers.get('X-Page-Count')) || undefined })
      } catch (e) {
        if (!controller.signal.aborted) setLoaded({ key, error: String(e) })
      }
    })()
    return () => {
      controller.abort()
      if (url) URL.revokeObjectURL(url)
    }
  }, [itemId, page, key])
  return { ...loaded, loading: loaded.key !== key }
}
