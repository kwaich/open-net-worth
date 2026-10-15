import { useEffect, useMemo, useState } from 'react'
import { useSkills, type SkillCreateBody } from '../../hooks/useSkills'
import { useRegisterScreenFacts } from '../../lucius/useScreenContract'
import type {
  ClaudeSkillMetadata,
  OpenAISkillMetadata,
  Skill,
  SkillBundle,
  SelectedFile,
} from '../../types/skills'
import {
  Button,
  Cluster,
  Container,
  ErrorState,
  FormField,
  Modal,
  PageHeader,
  SegmentedControl,
  Stack,
  Text,
  TextArea,
  TextField,
  TabSkeleton,
} from '../ui'
import { authFetch } from '../../api/client'
import { SkillsTree } from '../skills/SkillsTree'
import { SkillDetailPane } from '../skills/SkillDetailPane'
import { CreateSkillMenu } from '../skills/CreateSkillMenu'
import { UploadSkillModal } from '../skills/UploadSkillModal'
import { BrowseStartersModal } from '../skills/BrowseStartersModal'
import {
  ClaudeCompatibilityFields,
  OpenAICompatibilityFields,
  type SkillEditorSection,
} from '../skills/SkillCompatibilityEditor'
import '../skills/skills.css'

const SKILL_EDITOR_SECTIONS = [
  { value: 'instructions' as const, label: 'Instructions' },
  { value: 'claude' as const, label: 'Claude Code' },
  { value: 'openai' as const, label: 'OpenAI' },
]

interface Props {
  // Open the chat panel with a prefilled message (the Run surface).
  onRunSkill: (text: string) => void
}

// eslint-disable-next-line react-refresh/only-export-components -- exercised independently so staging stays side-effect-free.
export function buildSkillRunMessage(skill: Skill): string {
  // `id` is the persisted/direct-invocation identity. `name` is a mutable display
  // label, so using it here would turn a rename into a broken run command.
  return `/${skill.id}`
}

export function Skills({ onRunSkill }: Props) {
  const { skills, loading, error, refresh, createSkill, updateSkill, deleteSkill } = useSkills()

  // ── Selection state ──────────────────────────────────────────────────────────
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null)
  const [selectedFile, setSelectedFile] = useState<SelectedFile | null>(null)
  const [expandedSkills, setExpandedSkills] = useState<Set<string>>(new Set())

  // Bundle cache: Record<skillId, SkillBundle>. Prefetched for ALL skills in ONE request on mount
  // (see effect below) so expanding any skill is instant — no per-skill loading spinner.
  const [bundleCache, setBundleCache] = useState<Record<string, SkillBundle>>({})
  const [bundleLoading, setBundleLoading] = useState<Record<string, boolean>>({})
  const [bundleError, setBundleError] = useState<Record<string, boolean>>({})

  // Eager bulk prefetch: load every skill's full file tree up front in ONE request, so expanding
  // any skill is instant (no per-skill spinner). Re-runs when a skill is added/edited/removed
  // (keyed on the id+version+timestamp signature) so the tree reflects edits; fresh bulk wins.
  const bundleSignature = useMemo(
    () => skills.map((s) => `${s.id}:${s.updated_at ?? ''}`).join('|'),
    [skills],
  )
  useEffect(() => {
    if (!skills.length) return
    let cancelled = false
    authFetch('/api/skills/bundles')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((all: Record<string, SkillBundle>) => {
        if (!cancelled && all && typeof all === 'object' && !Array.isArray(all)) {
          const valid = Object.fromEntries(
            Object.entries(all).filter(
              ([, bundle]) => bundle && typeof bundle === 'object' && bundle.files,
            ),
          ) as Record<string, SkillBundle>
          setBundleCache((prev) => ({ ...prev, ...valid }))
        }
      })
      .catch(() => {
        /* best-effort — the per-skill fetch on expand remains the fallback */
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bundleSignature])

  // ── Mobile detail slide ──────────────────────────────────────────────────────
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false)

  // ── Search ───────────────────────────────────────────────────────────────────
  const [searchQuery, setSearchQuery] = useState('')

  // ── Action modals ────────────────────────────────────────────────────────────
  const [editing, setEditing] = useState<Skill | 'new' | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<Skill | null>(null)
  const [showUpload, setShowUpload] = useState(false)
  const [showStarters, setShowStarters] = useState(false)

  // ── Status ───────────────────────────────────────────────────────────────────
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyToggle, setBusyToggle] = useState<string | null>(null)

  // ── Derived: selected skill ──────────────────────────────────────────────────
  const selectedSkill = useMemo(
    () => skills.find((s) => s.id === selectedSkillId) ?? null,
    [skills, selectedSkillId],
  )

  // ── Bundle fetch ─────────────────────────────────────────────────────────────
  function fetchBundle(skillId: string) {
    if (bundleCache[skillId] || bundleLoading[skillId]) return
    setBundleLoading((prev) => ({ ...prev, [skillId]: true }))
    setBundleError((prev) => ({ ...prev, [skillId]: false }))
    authFetch(`/api/skills/${encodeURIComponent(skillId)}/bundle`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((b: SkillBundle) => {
        if (!b || typeof b !== 'object' || !b.files) throw new Error('Invalid skill bundle')
        setBundleCache((prev) => ({ ...prev, [skillId]: b }))
      })
      .catch(() => {
        setBundleError((prev) => ({ ...prev, [skillId]: true }))
      })
      .finally(() => {
        setBundleLoading((prev) => ({ ...prev, [skillId]: false }))
      })
  }

  // ── Handlers ─────────────────────────────────────────────────────────────────

  function onSelectSkill(skillId: string) {
    setSelectedSkillId(skillId)
    // Clear file selection when switching skills.
    setSelectedFile(null)
    setMobileDetailOpen(true)
    // EVERY skill has a virtual file tree (SKILL.md + scripts/… as applicable),
    // synthesized server-side — expand it and lazily fetch the bundle for all skills.
    setExpandedSkills((prev) => new Set([...prev, skillId]))
    fetchBundle(skillId)
  }

  function onExpandSkill(skillId: string) {
    const isExpanded = expandedSkills.has(skillId)
    if (isExpanded) {
      setExpandedSkills((prev) => {
        const next = new Set(prev)
        next.delete(skillId)
        return next
      })
    } else {
      setExpandedSkills((prev) => new Set([...prev, skillId]))
      fetchBundle(skillId)
    }
  }

  function onSelectFile(file: SelectedFile) {
    setSelectedFile(file)
    setSelectedSkillId(file.skillId)
    setMobileDetailOpen(true)
  }

  async function onToggleEnabled(skill: Skill) {
    setActionError(null)
    setBusyToggle(skill.id)
    const res = await updateSkill(skill.id, { enabled: !skill.enabled })
    setBusyToggle(null)
    if (!res.ok) setActionError(res.error || 'Could not update the skill.')
  }

  async function onDelete(skill: Skill) {
    setActionError(null)
    const res = await deleteSkill(skill.id)
    if (!res.ok) setActionError(res.error || 'Could not delete the skill.')
    setConfirmDelete(null)
    // If the deleted skill was selected, clear selection.
    if (selectedSkillId === skill.id) {
      setSelectedSkillId(null)
      setSelectedFile(null)
      setMobileDetailOpen(false)
    }
  }

  function onRun(skill: Skill) {
    onRunSkill(buildSkillRunMessage(skill))
  }

  // "Create with Lucius" — prefill chat to run the skill-creator skill.
  function onCreateWithLucius() {
    onRunSkill('/skill-creator')
  }

  useRegisterScreenFacts(() => {
    const on = skills.filter((sk) => sk.enabled)
    const facts: [string, string][] = [
      ['Skills', String(skills.length)],
      ['Enabled', String(on.length)],
    ]
    if (selectedSkill) facts.push(['Viewing skill', selectedSkill.name])
    return facts
  })

  const header = (
    <PageHeader
      title="Skills"
      subtitle="Reusable playbooks Lucius can run for you — one-click routines"
      actions={
        <CreateSkillMenu
          onCreateWithLucius={onCreateWithLucius}
          onWriteInstructions={() => setEditing('new')}
          onUpload={() => setShowUpload(true)}
          onBrowseStarters={() => setShowStarters(true)}
        />
      }
    />
  )

  if (loading) {
    // The header is static — it stays up; only the skill tree is a skeleton.
    return (
      <Container size="wide" className="skills-page">
        <Stack gap="--space-6" className="skills-page__stack">
          {header}
          <TabSkeleton variant="skills" label="Loading skills…" header={false} />
        </Stack>
      </Container>
    )
  }

  if (error) {
    return (
      <Container size="wide">
        <ErrorState message={error} onRetry={refresh} />
      </Container>
    )
  }

  return (
    <Container
      size="wide"
      className="skills-page"
      data-ctx-region="content"
      data-ctx-title="Skills"
    >
      <Stack gap="--space-6" className="skills-page__stack">
        {header}

        {actionError && (
          <Cluster justify="between" wrap={false}>
            <Text tone="bad" size="sm">
              {actionError}
            </Text>
            <Button variant="ghost" size="sm" onClick={() => setActionError(null)}>
              Dismiss
            </Button>
          </Cluster>
        )}

        {/* ── Two-pane workspace ── */}
        <div className="skills-workspace">
          <SkillsTree
            skills={skills}
            selectedSkillId={selectedSkillId}
            selectedFile={selectedFile}
            onSelectSkill={onSelectSkill}
            onSelectFile={onSelectFile}
            onCreateFirstSkill={() => setEditing('new')}
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            bundles={bundleCache}
            bundleLoading={bundleLoading}
            onExpandSkill={onExpandSkill}
            expandedSkills={expandedSkills}
          />

          <SkillDetailPane
            skill={selectedSkill}
            selectedFile={selectedFile}
            bundle={selectedSkillId ? (bundleCache[selectedSkillId] ?? null) : null}
            bundleLoading={selectedSkillId ? (bundleLoading[selectedSkillId] ?? false) : false}
            bundleError={selectedSkillId ? (bundleError[selectedSkillId] ?? false) : false}
            mobileActive={mobileDetailOpen}
            onToggleEnabled={onToggleEnabled}
            toggling={busyToggle === selectedSkillId}
            onEdit={(skill) => setEditing(skill)}
            onDelete={(skill) => setConfirmDelete(skill)}
            onRun={onRun}
            onChanged={refresh}
            onBack={() => setMobileDetailOpen(false)}
          />
        </div>
      </Stack>

      {/* ── Skill editor (create / edit) ── */}
      {editing && (
        <SkillEditor
          skill={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onCreate={createSkill}
          onUpdate={updateSkill}
        />
      )}

      {/* ── Upload modal ── */}
      {showUpload && (
        <UploadSkillModal
          onClose={() => setShowUpload(false)}
          onInstalled={(skill) => {
            setShowUpload(false)
            refresh()
            setSelectedSkillId(skill.id)
            setMobileDetailOpen(true)
          }}
        />
      )}

      {/* ── Browse starters modal ── */}
      {showStarters && (
        <BrowseStartersModal
          onClose={() => setShowStarters(false)}
          onAdded={(skill) => {
            refresh()
            setSelectedSkillId(skill.id)
            setMobileDetailOpen(true)
          }}
        />
      )}

      {/* ── Confirm delete ── */}
      {confirmDelete && (
        <Modal
          open
          size="sm"
          title={`Delete "${confirmDelete.name}"?`}
          onClose={() => setConfirmDelete(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setConfirmDelete(null)}>
                Cancel
              </Button>
              <Button variant="danger" onClick={() => onDelete(confirmDelete)}>
                Delete
              </Button>
            </>
          }
        >
          <Text tone="muted">
            This removes the skill permanently. Lucius will no longer be able to run it.
          </Text>
        </Modal>
      )}
    </Container>
  )
}

// ── Run-with-params modal ────────────────────────────────────────────────────

// ── Create / edit modal ──────────────────────────────────────────────────────

// Live-enforce the standard Agent-Skill `name` slug as the user types or pastes: lowercase,
// disallowed runs → a single hyphen, ≤64 chars. Leading/trailing hyphens are kept while typing
// (so "monthly-" then "close" works) and stripped server-side on save (normalize_skill_name).
function slugifyLiveName(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .slice(0, 64)
}

function SkillEditor({
  skill,
  onClose,
  onCreate,
  onUpdate,
}: {
  skill: Skill | null
  onClose: () => void
  onCreate: (body: SkillCreateBody) => Promise<{ ok: boolean; error?: string }>
  onUpdate: (
    id: string,
    changes: Record<string, unknown>,
  ) => Promise<{ ok: boolean; error?: string }>
}) {
  const isNew = skill === null
  const [name, setName] = useState(skill?.name ?? '')
  const [description, setDescription] = useState(skill?.description ?? '')
  const [body, setBody] = useState(skill?.body ?? '')
  const [claude, setClaude] = useState<ClaudeSkillMetadata>(skill?.claude ?? {})
  const [openai, setOpenai] = useState<OpenAISkillMetadata>(skill?.openai ?? {})
  const [section, setSection] = useState<SkillEditorSection>('instructions')
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  async function onSave() {
    setFormError(null)
    if (!name.trim()) {
      setFormError('A skill needs a name.')
      return
    }
    if (!description.trim()) {
      setSection('instructions')
      setFormError('A skill needs a description so Lucius knows when to use it.')
      return
    }
    setSaving(true)
    const compatibility = {
      ...(Object.keys(claude).length > 0 ? { claude } : {}),
      ...(Object.keys(openai).length > 0 ? { openai } : {}),
    }
    const payload = {
      name: name.trim(),
      description,
      body,
      ...(isNew ? compatibility : { claude, openai }),
    }
    const res = isNew ? await onCreate(payload) : await onUpdate(skill!.id, payload)
    setSaving(false)
    if (res.ok) onClose()
    else setFormError(res.error || 'Could not save the skill.')
  }

  return (
    <Modal
      open
      size="xl"
      title={isNew ? 'New skill' : `Edit "${skill!.name}"`}
      onClose={onClose}
      closeOnBackdrop={false}
      dragToDismiss={false}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" loading={saving} onClick={onSave}>
            {isNew ? 'Create skill' : 'Save changes'}
          </Button>
        </>
      }
    >
      <Stack gap="--space-6">
        <SegmentedControl
          ariaLabel="Skill editor section"
          options={SKILL_EDITOR_SECTIONS}
          value={section}
          onChange={setSection}
        />

        {section === 'instructions' && (
          <Stack gap="--space-6">
            <FormField
              htmlFor="skill-name"
              label="Name"
              required
              help="Lowercase letters, numbers, and hyphens (e.g. monthly-close)."
            >
              <TextField
                id="skill-name"
                value={name}
                placeholder="e.g. monthly-close"
                onChange={(e) => setName(slugifyLiveName(e.target.value))}
              />
            </FormField>

            <FormField htmlFor="skill-desc" label="Description" required>
              <TextField
                id="skill-desc"
                value={description}
                placeholder="One line — what this skill does"
                onChange={(e) => setDescription(e.target.value)}
              />
            </FormField>

            <FormField htmlFor="skill-body" label="Instructions (markdown)">
              <TextArea
                id="skill-body"
                rows={9}
                value={body}
                placeholder={
                  '1. Read the requested month from the conversation.\n2. Call get_spending_summary.\n3. …'
                }
                onChange={(e) => setBody(e.target.value)}
              />
            </FormField>
          </Stack>
        )}

        {section === 'claude' && <ClaudeCompatibilityFields value={claude} onChange={setClaude} />}

        {section === 'openai' && <OpenAICompatibilityFields value={openai} onChange={setOpenai} />}

        {formError && (
          <div role="alert">
            <Text tone="bad" size="sm">
              {formError}
            </Text>
          </div>
        )}
      </Stack>
    </Modal>
  )
}
