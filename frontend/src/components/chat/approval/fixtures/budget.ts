import type { ProposedChange } from '../../../../types/chat'
import type { CardFixture } from './types'

/** Synthetic cards in the shapes the planning tools stage (backend/ai/tools/planning.py,
 * _budget_confirm.py). A card carries each envelope / group id with the name the backend resolved
 * beside it (`with_names`); one fixture keeps a card staged before names existed (id only). */
function staged(
  type: string,
  grantTool: string,
  display: Record<string, unknown>,
  n: number,
): ProposedChange {
  return {
    id: `toolu_budget_${n}`,
    type,
    grant_tool: grantTool,
    tool_use_id: `toolu_budget_${n}`,
    display,
  } as ProposedChange
}

function permission(tool: string, args: Record<string, unknown>, n: number): ProposedChange {
  return staged('tool_permission', tool, { tool, arguments: args }, n)
}

export const BUDGET_FIXTURES: CardFixture[] = [
  {
    name: 'assign · envelope',
    change: staged(
      'assign',
      'assign_budget',
      {
        op: 'Assign',
        month: '2026-06',
        envelope_id: 'env_groceries',
        envelope_name: 'Groceries',
        amount: 400,
      },
      1,
    ),
  },
  {
    name: 'assign · staged before names (id only)',
    change: staged(
      'assign',
      'assign_budget',
      { op: 'Assign', month: '2026-06', envelope_id: 'env_groceries', amount: 400 },
      2,
    ),
  },
  {
    name: 'move_money · between envelopes',
    change: staged(
      'move_money',
      'move_money',
      {
        op: 'Move money',
        month: '2026-06',
        from_envelope_id: 'env_dining',
        from_envelope_name: 'Dining',
        to_envelope_id: 'env_groceries',
        to_envelope_name: 'Groceries',
        amount: 50,
      },
      3,
    ),
  },
  {
    name: 'move_money · out of Ready to Assign',
    change: staged(
      'move_money',
      'move_money',
      {
        op: 'Move money',
        month: '2026-06',
        from_envelope_id: '__rta',
        from_envelope_name: 'Ready to Assign',
        to_envelope_id: 'env_travel',
        to_envelope_name: 'Travel',
        amount: 120.5,
      },
      4,
    ),
  },
  {
    name: 'set_target · monthly',
    change: staged(
      'set_target',
      'set_envelope_targets',
      {
        op: 'Set target',
        envelope_id: 'env_rent',
        envelope_name: 'Rent',
        target_type: 'monthly',
        amount: 1800,
        date: null,
        snooze_until: null,
        needs_whole_amount: null,
      },
      5,
    ),
  },
  {
    name: 'set_target · savings by date, balance progress',
    change: staged(
      'set_target',
      'set_envelope_targets',
      {
        op: 'Set target',
        envelope_id: 'env_vacation',
        envelope_name: 'Vacation',
        target_type: 'by_date',
        amount: 3000,
        date: '2027-03-31',
        snooze_until: null,
        needs_whole_amount: false,
      },
      6,
    ),
  },
  {
    name: 'set_target · clear',
    change: staged(
      'set_target',
      'set_envelope_targets',
      {
        op: 'Clear target',
        envelope_id: 'env_gifts',
        envelope_name: 'Gifts',
        target_type: null,
        amount: 0,
      },
      7,
    ),
  },
  {
    name: 'set_target · snooze',
    change: staged(
      'set_target',
      'set_envelope_targets',
      {
        op: 'Snooze target',
        envelope_id: 'env_gifts',
        envelope_name: 'Gifts',
        target_type: null,
        amount: 0,
        snooze_until: '2026-12',
      },
      8,
    ),
  },
  {
    name: 'group_create',
    change: staged('group_create', 'create_envelope_group', { op: 'Create group', name: 'Fun' }, 9),
  },
  {
    name: 'group_rename',
    change: staged(
      'group_rename',
      'rename_envelope_groups',
      { op: 'Rename group', group_id: 'grp_extras', group_name: 'Extras', name: 'Lifestyle' },
      10,
    ),
  },
  {
    name: 'group_reorder',
    change: staged(
      'group_reorder',
      'reorder_envelope_groups',
      { op: 'Reorder groups', order: ['Bills', 'Everyday', 'Savings', 'Fun'] },
      11,
    ),
  },
  {
    name: 'group_delete',
    change: staged(
      'group_delete',
      'delete_envelope_groups',
      { op: 'Delete group', group_id: 'grp_extras', group_name: 'Extras' },
      12,
    ),
  },
  {
    name: 'move_category',
    change: staged(
      'move_category',
      'move_envelopes_to_group',
      {
        op: 'Move category',
        category: 'env_streaming',
        category_name: 'Streaming',
        group_id: 'grp_fun',
        group_name: 'Fun',
      },
      13,
    ),
  },
  {
    name: 'reorder_categories',
    change: staged(
      'reorder_categories',
      'reorder_envelopes_in_group',
      {
        op: 'Reorder envelopes',
        group_id: 'grp_everyday',
        group_name: 'Everyday',
        categories: ['env_groceries', 'env_dining', 'env_transport'],
        category_names: ['Groceries', 'Dining', 'Transport'],
      },
      14,
    ),
  },
  {
    name: 'apply_budget_operation · params',
    change: permission(
      'apply_budget_operation',
      {
        engine_id: 'zero_based',
        operation: 'assign',
        params: { month: '2026-06', envelope_id: 'env_food', amount: 500 },
      },
      15,
    ),
  },
  {
    name: 'apply_budget_operation · list param',
    change: permission(
      'apply_budget_operation',
      {
        engine_id: 'proportional',
        operation: 'set_income_categories',
        params: { category_ids: ['cat_salary', 'cat_bonus'] },
      },
      16,
    ),
  },
  {
    name: 'apply_budget_operation · no params',
    change: permission(
      'apply_budget_operation',
      { engine_id: 'proportional', operation: 'set_config', params: {} },
      17,
    ),
  },
  {
    name: 'set_home_budget_engine',
    change: permission('set_home_budget_engine', { engine_id: 'proportional' }, 18),
  },
]
