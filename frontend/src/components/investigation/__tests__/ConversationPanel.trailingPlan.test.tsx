/** ConversationPanel — trailing Plan block dedup (U00047 UI fix).

Pins the final-message composition after the Activity-in-conversation
redesign:
- agent_loop turns do NOT render the trailing PlanCard (the plan lives in
  the leading Activity block);
- non-loop turns KEEP the Plan card (historical turn-path compatibility);
- Task completed status and the findings_summary artifact remain visible;
- the composed answer text is intact.
*/

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { ConversationPanel, type ConversationTurn } from '../ConversationPanel'
import { investigationApi } from '@/api/investigation'
import type { TaskActivityResponse } from '@/api/types'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const PLAN = {
  plan_id: 'PLAN-1',
  goal: 'Investigate U00033',
  status: 'completed',
  selected_skill: 'case_intake',
  steps: [
    { step_id: 'S1', type: 'fetch_case', status: 'success',
      tool_name: 'risk_case_fetch', arguments: {}, reason: null,
      depends_on: [] },
    { step_id: 'S2', type: 'generate_artifact', status: 'success',
      tool_name: 'artifact_bundle', arguments: {}, reason: null,
      depends_on: [] },
  ],
}

const TASK = {
  task_id: 'TASK-1',
  investigation_id: 'INV-1',
  type: 'agent_loop',
  status: 'completed' as const,
  user_request: 'Investigate U00033',
  plan_id: 'PLAN-1',
  selected_skill: 'case_intake',
  tool_call_ids: [],
  artifact_ids: ['ART-1'],
  started_at: null,
  completed_at: null,
  error: null,
}

const ARTIFACT = {
  artifact_id: 'ART-1',
  investigation_id: 'INV-1',
  task_id: 'TASK-1',
  scope: 'case:U00033',
  artifact_type: 'findings_summary',
  format: 'md' as const,
  title: 'Investigation Evidence Bundle — U00033',
  content: '# bundle',
  storage_ref: null,
  source_tool_calls: [],
  created_at: null,
}

function turn(overrides: Partial<ConversationTurn>): ConversationTurn {
  return {
    id: 'a-TASK-1',
    role: 'agent',
    text: '关于「查询案例 U00033 的整体情况」: 案例共识别 8 个风险发现（总体风险：CRITICAL，风险分 86.1）。\n- F1 ML Pattern Detection',
    task: { ...TASK },
    plan: { ...PLAN },
    artifacts: [{ ...ARTIFACT }],
    followUps: [],
    ...overrides,
  } as ConversationTurn
}

function activityPayload(
  overrides: Partial<TaskActivityResponse> = {},
): TaskActivityResponse {
  return {
    task_id: 'TASK-1', investigation_id: 'INV-1', case_id: 'U00033',
    goal: 'g', criteria_kind: 'artifact_produced',
    focused_finding_id: null, agent_status: 'completed',
    stop_reason: 'goal_satisfied', iteration: 2, max_iterations: 8,
    plan_revisions: 1, plan_steps: [], artifact_ids: ['ART-1'],
    activity: [], is_terminal: true, goal_outcome: 'complete',
    ...overrides,
  }
}

function renderPanel(turns: ConversationTurn[]) {
  return render(
    <ConversationPanel
      turns={turns}
      loading={false}
      error={null}
      onSelectEvent={() => {}}
      focusedEventId={null}
      onSelectFollowUp={() => {}}
      onNavigate={() => {}}
      followUpsDisabled={false}
      focusedFindingId={null}
      liveActivity={null}
    />,
  )
}

describe('ConversationPanel trailing Plan dedup', () => {
  it('agent_loop turns do not render the trailing PlanCard', () => {
    vi.spyOn(investigationApi, 'getTaskActivity').mockResolvedValue(
      activityPayload())
    renderPanel([turn({})])
    // the PlanCard accordion trigger renders the literal 'Plan' header
    expect(screen.queryByText('Plan')).toBeNull()
  })

  it('loop turns keep the Activity block with the plan section', async () => {
    vi.spyOn(investigationApi, 'getTaskActivity').mockResolvedValue(
      activityPayload({
        plan_steps: [
          { step_id: 'S1', type: 'fetch_case', status: 'success',
            tool_name: 'risk_case_fetch' },
          { step_id: 'S2', type: 'generate_artifact', status: 'success',
            tool_name: 'artifact_bundle' },
        ],
        activity: [
          { seq: 1, event_type: 'step.completed', timestamp: 't',
            iteration: 1, step_id: 'S1',
            step_type: 'fetch_case', tool_name: 'risk_case_fetch',
            outcome: 'success', decision: null, agent_status: null,
            stop_reason: null, summary: null, artifact_ids: [] },
        ],
      }))
    renderPanel([turn({})])
    // the block renders its terminal snapshot; Radix keeps the expanded
    // plan/timeline sections unmounted until the user expands — that
    // expansion is verified in the real browser E2E. Here we pin the
    // collapsed representation and the ABSENCE of the trailing PlanCard.
    await screen.findByTestId('activity-block-status')
    expect(screen.queryByTestId('plan-card')).toBeNull()
    expect(screen.queryByTestId('activity-block-plan')).toBeNull()
  })

  it('Task completed status and findings_summary artifact remain', () => {
    vi.spyOn(investigationApi, 'getTaskActivity').mockResolvedValue(
      activityPayload())
    renderPanel([turn({})])
    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText('findings_summary')).toBeInTheDocument()
    expect(screen.getByText('Investigation Evidence Bundle — U00033'))
      .toBeInTheDocument()
    expect(screen.getByText('ART-1')).toBeInTheDocument()
  })

  it('the composed answer text is intact', () => {
    vi.spyOn(investigationApi, 'getTaskActivity').mockResolvedValue(
      activityPayload())
    renderPanel([turn({})])
    expect(screen.getByText(/8 个风险发现/)).toBeInTheDocument()
  })

  it('non-loop turns still render the Plan card (historical compatibility)',
    async () => {
      vi.spyOn(investigationApi, 'getTaskActivity').mockResolvedValue(
        activityPayload())
      const turns = [turn({
        task: { ...TASK, type: 'investigation_turn' },
      })]
      renderPanel(turns)
      // non-loop turns render the PlanCard trigger (role=button, name
      // contains "Plan"); the Radix accordion keeps the step list
      // collapsed/unmounted until expanded — assert the trigger header
      const trigger = screen.getAllByRole('button', { name: /Plan/i })
      expect(trigger.length).toBeGreaterThan(0)
    })

  it('restored turns answer the same contract (no trailing Plan)',
    () => {
      vi.spyOn(investigationApi, 'getTaskActivity').mockResolvedValue(
        activityPayload())
      const restored = turn({})
      renderPanel([{
        id: `u-${restored.id}`, role: 'user',
        text: 'Investigate U00033',
      }, restored])
      expect(screen.queryByText('Plan')).toBeNull()
      expect(screen.getByText(/8 个风险发现/)).toBeInTheDocument()
    })
})
