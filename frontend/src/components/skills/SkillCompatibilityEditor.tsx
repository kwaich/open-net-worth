import type {
  ClaudeSkillMetadata,
  OpenAISkillDependency,
  OpenAISkillMetadata,
} from '../../types/skills'
import {
  Button,
  Checkbox,
  FormField,
  Grid,
  Panel,
  SegmentedControl,
  Select,
  Stack,
  Text,
  TextArea,
  TextField,
} from '../ui'

export type SkillEditorSection = 'instructions' | 'claude' | 'openai'

type InvocationMode = 'both' | 'manual' | 'model'

function invocationMode(claude: ClaudeSkillMetadata): InvocationMode {
  if (claude.disable_model_invocation) return 'manual'
  if (claude.user_invocable === false) return 'model'
  return 'both'
}

function lines(value: string[] | undefined): string {
  return (value ?? []).join('\n')
}

function parseLines(value: string): string[] {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

export function ClaudeCompatibilityFields({
  value,
  onChange,
}: {
  value: ClaudeSkillMetadata
  onChange: (next: ClaudeSkillMetadata) => void
}) {
  const patch = (changes: Partial<ClaudeSkillMetadata>) => onChange({ ...value, ...changes })
  const mode = invocationMode(value)

  return (
    <Stack gap="--space-5">
      <Text tone="muted" size="sm">
        These fields make the exported folder native to Claude Code. ONW honors invocation
        visibility and arguments; Claude-only model, fork, hook, path, and shell runtime behavior
        remains compatibility metadata here.
      </Text>
      <Panel title="Invocation" className="skill-compat-panel">
        <Stack gap="--space-4">
          <SegmentedControl
            ariaLabel="Claude invocation"
            value={mode}
            options={[
              { value: 'both', label: 'Both' },
              { value: 'manual', label: 'Manual only' },
              { value: 'model', label: 'Model only' },
            ]}
            onChange={(next) =>
              patch({
                disable_model_invocation: next === 'manual',
                user_invocable: next !== 'model',
              })
            }
          />
          <FormField htmlFor="skill-when-to-use" label="When to use">
            <TextArea
              id="skill-when-to-use"
              rows={3}
              value={value.when_to_use ?? ''}
              placeholder="Trigger phrases and situations Claude should recognize"
              onChange={(event) => patch({ when_to_use: event.target.value })}
            />
          </FormField>
          <Grid min={260} gap="--space-4">
            <FormField
              htmlFor="skill-argument-hint"
              label="Argument hint"
              help="Shown in Claude Code autocomplete, for example [pr-number] [format]."
            >
              <TextField
                id="skill-argument-hint"
                value={value.argument_hint ?? ''}
                placeholder="[account] [date-range]"
                onChange={(event) => patch({ argument_hint: event.target.value })}
              />
            </FormField>
            <FormField
              htmlFor="skill-named-arguments"
              label="Named arguments"
              help="Space-separated names for $name placeholders; $ARGUMENTS and $0, $1 also work."
            >
              <TextField
                id="skill-named-arguments"
                value={(value.arguments ?? []).join(' ')}
                placeholder="account date_range"
                onChange={(event) =>
                  patch({ arguments: event.target.value.split(/\s+/).filter(Boolean) })
                }
              />
            </FormField>
          </Grid>
        </Stack>
      </Panel>

      <Grid min={300} gap="--space-4">
        <Panel title="Execution" className="skill-compat-panel">
          <Stack gap="--space-4">
            <FormField htmlFor="skill-claude-model" label="Model">
              <TextField
                id="skill-claude-model"
                value={value.model ?? ''}
                placeholder="inherit"
                onChange={(event) => patch({ model: event.target.value })}
              />
            </FormField>
            <FormField htmlFor="skill-claude-effort" label="Effort">
              <Select
                id="skill-claude-effort"
                value={value.effort ?? ''}
                options={[
                  { value: '', label: 'Inherit' },
                  { value: 'low', label: 'Low' },
                  { value: 'medium', label: 'Medium' },
                  { value: 'high', label: 'High' },
                  { value: 'xhigh', label: 'Extra high' },
                  { value: 'max', label: 'Maximum' },
                ]}
                onChange={(event) => patch({ effort: event.target.value })}
              />
            </FormField>
            <FormField htmlFor="skill-claude-context" label="Context">
              <Select
                id="skill-claude-context"
                value={value.context ?? ''}
                options={[
                  { value: '', label: 'Inline' },
                  { value: 'fork', label: 'Forked subagent' },
                ]}
                onChange={(event) => patch({ context: event.target.value })}
              />
            </FormField>
            {value.context === 'fork' && (
              <>
                <FormField htmlFor="skill-claude-agent" label="Agent">
                  <TextField
                    id="skill-claude-agent"
                    value={value.agent ?? ''}
                    placeholder="general-purpose"
                    onChange={(event) => patch({ agent: event.target.value })}
                  />
                </FormField>
                <Checkbox
                  id="skill-claude-background"
                  label="Run in background"
                  checked={value.background !== false}
                  onChange={(event) => patch({ background: event.target.checked })}
                />
              </>
            )}
          </Stack>
        </Panel>

        <Panel title="Environment" className="skill-compat-panel">
          <Stack gap="--space-4">
            <FormField
              htmlFor="skill-claude-paths"
              label="Allowed paths"
              help="One Claude path glob per line. This limits automatic activation in Claude Code."
            >
              <TextArea
                id="skill-claude-paths"
                rows={4}
                value={lines(value.paths)}
                placeholder={'backend/**\ntests/**'}
                onChange={(event) => patch({ paths: parseLines(event.target.value) })}
              />
            </FormField>
            <FormField htmlFor="skill-claude-shell" label="Dynamic-context shell">
              <Select
                id="skill-claude-shell"
                value={value.shell ?? ''}
                options={[
                  { value: '', label: 'Bash (default)' },
                  { value: 'bash', label: 'Bash' },
                  { value: 'powershell', label: 'PowerShell' },
                ]}
                onChange={(event) => patch({ shell: event.target.value })}
              />
            </FormField>
            <Text tone="faint" size="xs">
              ONW preserves Claude dynamic-context syntax but never executes host-shell commands
              while loading or saving a skill.
            </Text>
          </Stack>
        </Panel>
      </Grid>
    </Stack>
  )
}

function dependencyPatch(
  rows: OpenAISkillDependency[],
  index: number,
  patch: Partial<OpenAISkillDependency>,
): OpenAISkillDependency[] {
  return rows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row))
}

export function OpenAICompatibilityFields({
  value,
  onChange,
}: {
  value: OpenAISkillMetadata
  onChange: (next: OpenAISkillMetadata) => void
}) {
  const ui = value.interface ?? {}
  const dependencies = value.dependencies?.tools ?? []
  const setInterface = (patch: Record<string, string>) =>
    onChange({ ...value, interface: { ...ui, ...patch } })
  const setDependencies = (tools: OpenAISkillDependency[]) =>
    onChange({
      ...value,
      dependencies: { ...(value.dependencies ?? {}), tools },
    })

  return (
    <Stack gap="--space-5">
      <Panel title="Invocation" className="skill-compat-panel">
        <Checkbox
          id="skill-openai-implicit"
          label="Allow implicit invocation"
          description="Codex may select this skill when the user request matches its description."
          checked={value.policy?.allow_implicit_invocation !== false}
          onChange={(event) =>
            onChange({
              ...value,
              policy: { ...(value.policy ?? {}), allow_implicit_invocation: event.target.checked },
            })
          }
        />
      </Panel>

      <Panel title="Interface" className="skill-compat-panel">
        <Grid min={260} gap="--space-4">
          <FormField htmlFor="skill-openai-display-name" label="Display name">
            <TextField
              id="skill-openai-display-name"
              value={String(ui.display_name ?? '')}
              onChange={(event) => setInterface({ display_name: event.target.value })}
            />
          </FormField>
          <FormField htmlFor="skill-openai-short-description" label="Short description">
            <TextField
              id="skill-openai-short-description"
              value={String(ui.short_description ?? '')}
              onChange={(event) => setInterface({ short_description: event.target.value })}
            />
          </FormField>
          <FormField htmlFor="skill-openai-icon-small" label="Small icon path">
            <TextField
              id="skill-openai-icon-small"
              value={String(ui.icon_small ?? '')}
              placeholder="./assets/icon.svg"
              onChange={(event) => setInterface({ icon_small: event.target.value })}
            />
          </FormField>
          <FormField htmlFor="skill-openai-icon-large" label="Large icon path">
            <TextField
              id="skill-openai-icon-large"
              value={String(ui.icon_large ?? '')}
              placeholder="./assets/logo.png"
              onChange={(event) => setInterface({ icon_large: event.target.value })}
            />
          </FormField>
          <FormField htmlFor="skill-openai-brand-color" label="Brand color">
            <TextField
              id="skill-openai-brand-color"
              value={String(ui.brand_color ?? '')}
              placeholder="#3B82F6"
              onChange={(event) => setInterface({ brand_color: event.target.value })}
            />
          </FormField>
        </Grid>
        <FormField htmlFor="skill-openai-default-prompt" label="Default prompt">
          <TextArea
            id="skill-openai-default-prompt"
            rows={3}
            value={String(ui.default_prompt ?? '')}
            onChange={(event) => setInterface({ default_prompt: event.target.value })}
          />
        </FormField>
      </Panel>

      <Panel
        title="Dependencies"
        actions={
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setDependencies([...dependencies, { type: 'mcp', value: '' }])}
          >
            Add dependency
          </Button>
        }
        className="skill-compat-panel"
      >
        <Text tone="muted" size="xs">
          Declared for Codex and ChatGPT hosts. ONW preserves these entries but does not install or
          connect dependencies automatically.
        </Text>
        {dependencies.length === 0 ? (
          <Text tone="faint" size="sm">
            No OpenAI dependencies declared.
          </Text>
        ) : (
          <Stack gap="--space-4">
            {dependencies.map((dependency, index) => (
              <div className="skill-dependency-row" key={index}>
                <Grid min={190} gap="--space-3">
                  <FormField htmlFor={`skill-dependency-type-${index}`} label="Type">
                    <TextField
                      id={`skill-dependency-type-${index}`}
                      value={dependency.type}
                      onChange={(event) =>
                        setDependencies(
                          dependencyPatch(dependencies, index, { type: event.target.value }),
                        )
                      }
                    />
                  </FormField>
                  <FormField htmlFor={`skill-dependency-value-${index}`} label="Identifier">
                    <TextField
                      id={`skill-dependency-value-${index}`}
                      value={dependency.value}
                      onChange={(event) =>
                        setDependencies(
                          dependencyPatch(dependencies, index, { value: event.target.value }),
                        )
                      }
                    />
                  </FormField>
                  <FormField htmlFor={`skill-dependency-description-${index}`} label="Description">
                    <TextField
                      id={`skill-dependency-description-${index}`}
                      value={dependency.description ?? ''}
                      onChange={(event) =>
                        setDependencies(
                          dependencyPatch(dependencies, index, {
                            description: event.target.value,
                          }),
                        )
                      }
                    />
                  </FormField>
                  <FormField htmlFor={`skill-dependency-transport-${index}`} label="Transport">
                    <TextField
                      id={`skill-dependency-transport-${index}`}
                      value={dependency.transport ?? ''}
                      placeholder="streamable_http"
                      onChange={(event) =>
                        setDependencies(
                          dependencyPatch(dependencies, index, { transport: event.target.value }),
                        )
                      }
                    />
                  </FormField>
                  <FormField htmlFor={`skill-dependency-url-${index}`} label="URL">
                    <TextField
                      id={`skill-dependency-url-${index}`}
                      value={dependency.url ?? ''}
                      onChange={(event) =>
                        setDependencies(
                          dependencyPatch(dependencies, index, { url: event.target.value }),
                        )
                      }
                    />
                  </FormField>
                </Grid>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setDependencies(dependencies.filter((_row, rowIndex) => rowIndex !== index))
                  }
                >
                  Remove
                </Button>
              </div>
            ))}
          </Stack>
        )}
      </Panel>
    </Stack>
  )
}
