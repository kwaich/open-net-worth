import type { ProposedChange } from '../../../../types/chat'
import type { CardFixture } from './types'

/** Synthetic skill / agent cards in the shape the authoring tools stage
 * (backend/ai/tools/skills.py; the agent display mirrors the retired agents tool's
 * `_agent_display`, which still defines `ProposedChange.display`). Names are invented. */

const staged = (name: string, grantTool: string, change: Record<string, unknown>): CardFixture => {
  const id = `toolu_${name.replace(/[^a-z0-9]+/gi, '_').toLowerCase()}`
  return {
    name,
    change: { id, grant_tool: grantTool, ...change } as ProposedChange,
  }
}

const REVIEW_PROMPT = `You are a weekly money reviewer.
Every Sunday, read the last seven days of transactions and group them by category.
Flag any category that ran more than 20% over its envelope.
Never move money yourself; propose changes and explain each one in a sentence.`

const REVIEW_PROMPT_V2 = `You are a weekly money reviewer.
Every Sunday, read the last seven days of transactions and group them by category.
Flag any category that ran more than 10% over its envelope.
Never move money yourself; propose changes and explain each one in a sentence.
Keep the summary under 120 words.`

const COACH_PROMPT = `You are a budget coach. Each morning, compare month-to-date spending with the plan.
Message the owner only when a category is on track to overspend.`

export const SKILL_AGENT_FIXTURES: CardFixture[] = [
  staged('skill_create · new playbook', 'create_skill', {
    type: 'skill_create',
    skill: {
      name: 'Weekly review',
      description: 'Walk through last week’s spending and flag drift.',
      body: '1. Pull the last seven days.\n2. Group by category.\n3. Flag anything over its envelope.',
    },
    display: { name: 'Weekly review' },
  }),

  staged('skill_update · changed fields', 'update_skill', {
    type: 'skill_update',
    skill_id: 'skl_weekly_review',
    changes: {
      description: 'Walk through last week’s spending, flag drift and suggest fixes.',
      enabled: false,
    },
    display: { name: 'Weekly review', changed_fields: ['description', 'enabled'] },
  }),

  staged('skill_update · body edit', 'update_skill', {
    type: 'skill_update',
    skill_id: 'skl_weekly_review',
    changes: {
      body: {
        old: '3. Flag anything over its envelope.',
        new: '3. Flag anything over its envelope by more than 10%.',
      },
    },
    display: {
      name: 'Weekly review',
      changed_fields: ['body'],
      body_edit: {
        old: '3. Flag anything over its envelope.',
        new: '3. Flag anything over its envelope by more than 10%.',
        old_truncated: false,
        new_truncated: false,
        old_chars: 36,
        new_chars: 53,
      },
    },
  }),

  staged('skill_delete · saved playbook', 'delete_skill', {
    type: 'skill_delete',
    skill_id: 'skl_weekly_review',
    display: { name: 'Weekly review' },
  }),

  staged('agent_create · propose-only', 'create_agent', {
    type: 'agent_create',
    agent: {
      name: 'Budget coach',
      description: 'Watches month-to-date spending against the plan.',
      prompt: COACH_PROMPT,
      allowed_tools: ['get_budget_month', 'get_spending_summary', 'telegram_send_message'],
      model: 'claude-sonnet-4',
      channels: ['telegram'],
    },
    display: {
      name: 'Budget coach',
      description: 'Watches month-to-date spending against the plan.',
      prompt_preview: COACH_PROMPT.replace(/\s+/g, ' '),
      tools_used: 3,
      allowed_tools: ['get_budget_month', 'get_spending_summary', 'telegram_send_message'],
      model: 'claude-sonnet-4',
      channels: ['telegram'],
      auto_apply: null,
    },
  }),

  staged('agent_create · with auto-apply grant', 'create_agent', {
    type: 'agent_create',
    agent: {
      name: 'Bookkeeper',
      description: 'Categorizes new transactions as they arrive.',
      prompt:
        'Categorize every uncategorized transaction from the last day using the payee history.',
      allowed_tools: ['get_transactions', 'update_transactions'],
      model: null,
      channels: null,
    },
    display: {
      name: 'Bookkeeper',
      description: 'Categorizes new transactions as they arrive.',
      prompt_preview:
        'Categorize every uncategorized transaction from the last day using the payee history.',
      tools_used: 2,
      allowed_tools: ['get_transactions', 'update_transactions'],
      model: null,
      channels: null,
      auto_apply: {
        capabilities: ['categorize', 'tag'],
        unattended_capabilities: ['categorize'],
        max_writes_per_run: 25,
        max_amount_base: 500,
      },
    },
  }),

  staged('agent_update · prompt edit', 'update_agent', {
    type: 'agent_update',
    agent_id: 'agt_weekly_reviewer',
    changes: {
      prompt: {
        old: 'ran more than 20% over its envelope',
        new: 'ran more than 10% over its envelope',
      },
    },
    display: {
      name: 'Weekly reviewer',
      changed_fields: ['prompt'],
      prompt_edit: {
        old: 'ran more than 20% over its envelope',
        new: 'ran more than 10% over its envelope',
        old_truncated: false,
        new_truncated: false,
        old_chars: 36,
        new_chars: 36,
      },
    },
  }),

  staged('agent_update · whole prompt replaced', 'update_agent', {
    type: 'agent_update',
    agent_id: 'agt_weekly_reviewer',
    changes: { prompt: REVIEW_PROMPT_V2 },
    display: {
      name: 'Weekly reviewer',
      changed_fields: ['prompt'],
      prompt_preview: REVIEW_PROMPT_V2.replace(/\s+/g, ' '),
      prompt_old_preview: REVIEW_PROMPT.replace(/\s+/g, ' '),
      prompt_old: REVIEW_PROMPT,
    },
  }),

  staged('agent_update · tools, model, channels, enabled', 'update_agent', {
    type: 'agent_update',
    agent_id: 'agt_budget_coach',
    changes: {
      allowed_tools: ['get_budget_month', 'get_spending_summary'],
      model: 'claude-haiku-4',
      channels: [],
      enabled: false,
    },
    display: {
      name: 'Budget coach',
      changed_fields: ['allowed_tools', 'channels', 'enabled', 'model'],
      tools_used: 2,
      allowed_tools: ['get_budget_month', 'get_spending_summary'],
      model: 'claude-haiku-4',
      channels: [],
      enabled: false,
    },
  }),

  staged('agent_update · rename and description', 'update_agent', {
    type: 'agent_update',
    agent_id: 'agt_budget_coach',
    changes: { name: 'Spending coach', description: 'Flags categories drifting off plan.' },
    display: {
      name: 'Spending coach',
      changed_fields: ['description', 'name'],
      description: 'Flags categories drifting off plan.',
    },
  }),

  staged('agent_update · auto-apply change', 'update_agent', {
    type: 'agent_update',
    agent_id: 'agt_bookkeeper',
    changes: {
      auto_apply: {
        capabilities: ['categorize'],
        unattended_capabilities: ['categorize'],
        max_writes_per_run: 10,
      },
    },
    display: {
      name: 'Bookkeeper',
      changed_fields: ['auto_apply'],
      auto_apply: {
        capabilities: ['categorize'],
        unattended_capabilities: ['categorize'],
        max_writes_per_run: 10,
        max_amount_base: null,
      },
    },
  }),

  staged('agent_update · auto-apply revoked', 'update_agent', {
    type: 'agent_update',
    agent_id: 'agt_bookkeeper',
    changes: { auto_apply: null },
    display: { name: 'Bookkeeper', changed_fields: ['auto_apply'], auto_apply: null },
  }),
]
