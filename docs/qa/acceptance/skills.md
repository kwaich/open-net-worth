# Acceptance criteria — Skills tab

Interaction-level GIVEN/WHEN/THEN for the **Skills** tab. The QA agent must exercise every
criterion and report PASS/FAIL per ID.

---

### SKILLS-LOAD
- GIVEN I am on any tab
- WHEN I click "Skills" in the sidebar
- THEN the Skills tab loads a searchable master-detail workspace with a "+ New skill" button,
  no JS error and no blank screen. Starter skills (monthly-close, budget-review, etc.) appear
  on a fresh instance.

### SKILLS-CARD-BODY-OPENS-DETAIL
- GIVEN the Skills tab is showing skill cards
- WHEN I select a skill row in the tree (not its expand chevron)
- THEN its read-only detail pane opens showing the skill description and SKILL.md preview —
  not the edit form.

### SKILLS-RUN-NO-PARAMS
- GIVEN an enabled skill with no parameters
- WHEN I select it and click "Run" in its detail pane
- THEN the chat panel opens (if not already open) with the exact direct slash invocation
  built from the immutable skill id prefilled (for example `/budget-review`). It does not
  silently submit a chat turn.

### SKILLS-RUN-WITH-PARAMS
- GIVEN an enabled skill that declares one or more parameters (e.g. "month")
- WHEN I select it and click "Run" in its detail pane
- THEN the RunParamsModal opens first with a field for each parameter — NOT a direct chat
  send — and the Run button in the modal is disabled until all required fields are filled.

### SKILLS-RUN-PARAMS-SUBMIT
- GIVEN the RunParamsModal is open with required params filled
- WHEN I click Run in the modal
- THEN the modal closes AND the chat panel receives the exact slash invocation incorporating
  the values as shell-safe named arguments (e.g. `/monthly-close month='2026-05'`), ready
  for the user to send.

### SKILLS-CREATE-OPENS
- GIVEN I am on the Skills tab
- WHEN I click "+ New skill"
- AND choose "Write skill instructions"
- THEN the SkillEditor modal opens with blank Name, Description, Instructions, and tool
  selection fields — NOT the detail view or an error.

### SKILLS-CREATE-SAVE
- GIVEN the SkillEditor is open with a valid name (e.g. "Test Skill") and at least one tool
  checked
- WHEN I click "Create skill"
- THEN the modal closes AND the new skill appears as a selectable row in the tree, with the
  normalized name I entered.

### SKILLS-CREATE-VALIDATION
- GIVEN the SkillEditor is open with an empty name field
- WHEN I click "Create skill"
- THEN an inline error "A skill needs a name." is shown and the modal does NOT close.

### SKILLS-EDIT-PREPOPULATED
- GIVEN at least one custom skill (non-builtin) exists
- WHEN I select it and choose "Edit" from its actions menu
- THEN the SkillEditor modal opens with that skill's existing name, description, body, and
  allowed tools pre-populated — NOT blank fields.

### SKILLS-DISABLE-ENABLE
- GIVEN any skill card is visible
- WHEN I open the kebab and click "Disable"
- THEN the card shows a "Disabled" badge AND the "Run" button becomes disabled with a
  tooltip "Enable the skill to run it".

### SKILLS-DEGRADED-WARNING
- GIVEN a skill references a tool that no longer exists in the registry
- WHEN I view that skill's card
- THEN a warning message lists the missing tools AND the "Run" button is disabled — no silent
  "runs fine" state.

### SKILLS-EMPTY-STATE
- GIVEN all skills have been deleted (or the skills store is empty)
- WHEN I view the Skills tab
- THEN an empty state with guidance is shown (not a blank grey box), including a
  "Create your first skill" button.
