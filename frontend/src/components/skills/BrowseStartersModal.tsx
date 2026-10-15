import { useEffect, useState } from 'react'
import { Badge, Button, Cluster, ErrorState, Loading, Modal, Stack, Text } from '../ui'
import { authFetch } from '../../api/client'
import type { Skill, StarterSkill } from '../../types/skills'

interface BrowseStartersModalProps {
  onClose: () => void
  onAdded: (skill: Skill) => void
}

/**
 * Browse and add built-in starter skills not yet installed.
 * GET /api/skills/starters → StarterSkill[] (only starters absent from the store).
 * "Add" → POST /api/skills/starters/{id} → {ok, skill, already?} → refresh + onAdded.
 */
export function BrowseStartersModal({ onClose, onAdded }: BrowseStartersModalProps) {
  const [starters, setStarters] = useState<StarterSkill[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [adding, setAdding] = useState<string | null>(null) // id of skill being added
  const [addError, setAddError] = useState<string | null>(null)
  const [added, setAdded] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setLoadError(null)
      try {
        const res = await authFetch('/api/skills/starters')
        if (!res.ok) {
          const d = await res.json().catch(() => null)
          const msg =
            typeof d?.detail === 'string'
              ? d.detail
              : `Could not load starters (HTTP ${res.status})`
          if (!cancelled) setLoadError(msg)
          return
        }
        const data: StarterSkill[] = await res.json()
        if (!cancelled) setStarters(data)
      } catch {
        if (!cancelled) setLoadError('Could not reach the server.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [])

  async function addStarter(starter: StarterSkill) {
    setAdding(starter.id)
    setAddError(null)
    try {
      const res = await authFetch(`/api/skills/starters/${encodeURIComponent(starter.id)}`, {
        method: 'POST',
      })
      if (!res.ok) {
        const d = await res.json().catch(() => null)
        const msg =
          typeof d?.detail === 'string' ? d.detail : `Could not add starter (HTTP ${res.status})`
        setAddError(msg)
        return
      }
      const d = await res.json()
      setAdded((prev) => new Set([...prev, starter.id]))
      onAdded(d.skill)
    } catch {
      setAddError('Could not reach the server.')
    } finally {
      setAdding(null)
    }
  }

  return (
    <Modal
      open
      size="md"
      title="Browse starter skills"
      onClose={onClose}
      footer={
        <Cluster justify="end" gap="--space-2">
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
        </Cluster>
      }
    >
      <Stack gap="--space-4">
        <Text tone="muted" size="sm">
          Add built-in starter skills to your library. These seed the most common routines — you can
          customise them afterwards.
        </Text>

        {addError && (
          <div role="alert">
            <Text tone="bad" size="sm">
              {addError}
            </Text>
          </div>
        )}

        {loading ? (
          <Loading label="Loading starters…" />
        ) : loadError ? (
          <ErrorState message={loadError} />
        ) : starters.length === 0 ? (
          <div style={{ padding: 'var(--space-6)', textAlign: 'center' }}>
            <Text tone="faint" size="sm">
              You already have all available starter skills.
            </Text>
          </div>
        ) : (
          <div className="starter-list">
            {starters.map((s) => (
              <StarterRow
                key={s.id}
                starter={s}
                isAdded={added.has(s.id)}
                isAdding={adding === s.id}
                onAdd={() => void addStarter(s)}
              />
            ))}
          </div>
        )}
      </Stack>
    </Modal>
  )
}

function StarterRow({
  starter,
  isAdded,
  isAdding,
  onAdd,
}: {
  starter: StarterSkill
  isAdded: boolean
  isAdding: boolean
  onAdd: () => void
}) {
  return (
    <div className="starter-row">
      <div className="starter-row__info">
        <Stack gap="--space-1">
          <Text size="sm" weight={600}>
            {starter.name}
          </Text>
          {starter.description && (
            <Text tone="muted" size="xs">
              {starter.description}
            </Text>
          )}
        </Stack>
      </div>
      <div className="starter-row__action">
        {isAdded ? (
          <Badge tone="muted">Added</Badge>
        ) : (
          <Button
            variant="secondary"
            size="sm"
            loading={isAdding}
            disabled={isAdding}
            onClick={onAdd}
          >
            Add
          </Button>
        )}
      </div>
    </div>
  )
}
