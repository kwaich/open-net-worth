// Types for the skill registry (P2-SK1). Mirrors backend/db/skill_store.py +
// the GET /api/skills row projection (services/skills.to_list_row).

export type SkillAuthor = 'builtin' | 'user' | 'lucius'

export interface OpenAISkillDependency {
  type: string
  value: string
  description?: string
  transport?: string
  url?: string
}

export interface OpenAISkillMetadata {
  interface?: {
    display_name?: string
    short_description?: string
    icon_small?: string
    icon_large?: string
    brand_color?: string
    default_prompt?: string
    [key: string]: unknown
  }
  dependencies?: { tools?: OpenAISkillDependency[]; [key: string]: unknown }
  policy?: { allow_implicit_invocation?: boolean; products?: string[] }
}

export interface ClaudeSkillMetadata {
  when_to_use?: string
  argument_hint?: string
  arguments?: string[]
  disable_model_invocation?: boolean
  user_invocable?: boolean
  model?: string
  effort?: string
  context?: string
  agent?: string
  background?: boolean
  hooks?: Record<string, unknown>
  paths?: string[]
  shell?: string
}

// A skill record plus the gallery projection fields the API adds per row.
export interface Skill {
  id: string
  name: string
  description: string
  license?: string // optional SKILL.md frontmatter license (e.g. "Complete terms in LICENSE.txt")
  compatibility?: string // standard SKILL.md field — env requirements (≤500 chars)
  metadata?: Record<string, string> // standard SKILL.md field — arbitrary string→string map
  body: string
  claude?: ClaudeSkillMetadata
  openai?: OpenAISkillMetadata
  enabled: boolean
  author: SkillAuthor
  created_at: string
  updated_at: string
  // has_scripts is DERIVED: true iff the skill folder contains a scripts/ directory.
  has_scripts?: boolean
  // Present when a direct filesystem edit leaves SKILL.md or agents/openai.yaml invalid.
  // Invalid skills stay visible for repair but are fail-closed for every invocation path.
  validation_errors?: string[]
}

// GET /api/skills/{id}/bundle — a skill's FULL virtual file tree (synthesized for EVERY skill).
// `files` carries real bundle files (scripts/*, references/*, LICENSE.txt…); manifest is
// DERIVED from scripts/ on the server — manifest.json never appears in `files`.
// SKILL.md → `skill_md`; optional agents/openai.yaml is included in `files`.
export interface SkillBundle {
  skill_md: string
  manifest: { scripts?: { id: string; entry?: string; description?: string; timeout_s?: number }[] }
  files: Record<string, string> // {relative_path: source}
}

// A file selected in the master-detail tree — null = no file selected (prompt skill body shown).
export interface SelectedFile {
  skillId: string
  relativePath: string // 'SKILL.md' or 'scripts/foo.py' etc.
}

// One node in the derived file tree for a script skill bundle.
export interface FileNode {
  name: string
  path: string // relative path from bundle root (e.g. 'scripts/foo.py')
  isDir: boolean
  children?: FileNode[]
}

// One entry from GET /api/skills/starters — built-in starters not yet installed.
export interface StarterSkill {
  id: string
  name: string
  description: string
}
