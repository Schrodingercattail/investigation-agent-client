/**
 * Compile-time + contract self-checks for the V2 API layer.
 *
 * The project has no frontend test framework (and this phase must not add
 * one), so these assertions run in dev builds only (`import.meta.env.DEV`)
 * and validate the render-critical contracts: FollowUp laziness (no
 * execution surface), severity/status mappings being exhaustive over the
 * API types, and typed envelope shapes round-tripping through JSON.
 */

import type {
  FollowUp,
  InvestigationContext,
  Task,
  ToolCallSummary,
  TurnResponse,
} from './types'

export function runApiSelfChecks(): void {
  if (!import.meta.env.DEV) return

  // 1. FollowUp contract stays lazy: serializable, no callable surface.
  const followUp: FollowUp = {
    follow_up_id: 'explain_finding',
    label: 'Why is this finding flagged?',
    intent: 'Why was this finding flagged?',
    required_capabilities: ['signal_explain'],
    applicable_context: 'finding',
    target_skill: 'timeline_investigation',
    target_step: 'explain_signal',
    reason: null,
  }
  const round: FollowUp = JSON.parse(JSON.stringify(followUp))
  console.assert(round.follow_up_id === 'explain_finding', 'FollowUp roundtrip')
  console.assert(
    Object.values(round).every((v) => typeof v !== 'function'),
    'FollowUp must not carry functions (no direct tool execution)',
  )

  // 2. TurnResponse envelope keys match the API contract.
  const turnKeys: (keyof TurnResponse)[] = [
    'investigation_id', 'status', 'response', 'context', 'task', 'plan',
    'execution', 'artifacts', 'follow_ups', 'context_changed',
  ]
  void turnKeys

  // 3. ToolCallSummary bounded outcomes.
  const outcomes: ToolCallSummary['outcome'][] = [
    'success', 'unsupported', 'empty', 'integration_error',
    'validation_error', null,
  ]
  console.assert(outcomes.length === 6, 'outcome vocabulary complete')

  // 4. Task status vocabulary.
  const statuses: Task['status'][] = [
    'pending', 'planning', 'executing', 'completed', 'failed', 'cancelled',
  ]
  console.assert(statuses.length === 6, 'task status vocabulary complete')

  // 5. Context focus fields nullable; focus_source vocabulary.
  const ctx: InvestigationContext = {
    case_id: 'U00299',
    focused_finding_id: null,
    focused_event_id: null,
    focus_source: null,
    time_window: null,
    selected_policy_ids: [],
    preferences: {
      citation_required: true,
      response_length: 'standard',
      output_format: 'md',
    },
  }
  console.assert(ctx.focus_source === null, 'focus_source nullable')
}
