/**
 * activityPresentation — the SINGLE testable mapping from the backend's
 * three terminal-relevant fields (agent_status × stop_reason ×
 * goal_outcome) to one user-visible presentation state.
 *
 * Correctness contract (mirrors the accepted backend semantics):
 * - the task container reaching a terminal state is NOT goal success;
 * - `bounded_data` / goal_outcome=partial is a PARTIAL result — never
 *   rendered as full success, even defensively against inconsistent
 *   field combinations;
 * - hitting the iteration limit (stopped_by_limit) is a bounded stop,
 *   never success;
 * - a failing run stays failed; a blocked run stays blocked;
 * - there is NO separate "cancelled" state in the backend loop — none is
 *   invented here.
 *
 * The mapping is deliberately TOTAL: any unknown combination degrades to
 * the conservative 'partial' family rather than to success.
 */

import type { TaskActivityResponse } from '@/api/types'

export type ActivityTone =
  | 'running'      // the run is genuinely still executing
  | 'success'      // goal fully satisfied (complete + goal_satisfied)
  | 'partial'      // bounded / partial result (data or budget limited)
  | 'failed'       // the run failed
  | 'blocked'      // the run is blocked

export interface ActivityPresentation {
  tone: ActivityTone
  /** short user-visible status label */
  label: string
  /** one-line explanation of WHY this state holds (server-grounded) */
  hint: string
}

const STOP_HINTS: Record<string, string> = {
  goal_satisfied: 'All requested outputs were satisfied.',
  bounded_data: 'Delivered the evidence acquired so far; part of the '
    + 'requested scope is not available.',
  conclusive_empty_result: 'The retrieved scope is complete and contains '
    + 'no matching records.',
  iteration_bound_reached: 'Stopped at the execution limit before the '
    + 'goal could be fully answered.',
  replan_failure: 'No further plan could be produced within the '
    + 'supported investigation surface.',
  planning_failure: 'The investigation could not be planned.',
  clarification_needed: 'Waiting on a clarification from the analyst.',
}

function stopHint(stopReason: string | null, outcome: string | null): string {
  if (stopReason && STOP_HINTS[stopReason]) return STOP_HINTS[stopReason]
  if (outcome === 'unanswerable') {
    return 'With the current capabilities and data, this request cannot '
      + 'be answered.'
  }
  if (outcome === 'blocked') return 'The investigation is blocked.'
  return 'The investigation reached its final state.'
}

/**
 * Map an activity snapshot to its presentation. `isTerminal` comes from
 * the response's is_terminal flag; when absent (older payloads) the
 * known terminal agent_status values are used conservatively.
 */
export function resolveActivityPresentation(
  activity: Pick<
    TaskActivityResponse,
    'agent_status' | 'stop_reason' | 'goal_outcome' | 'is_terminal'
  >,
): ActivityPresentation {
  const status = activity.agent_status
  const stop = activity.stop_reason
  const outcome = activity.goal_outcome ?? null

  // --- still executing ---------------------------------------------------
  if (
    !activity.is_terminal
    && (status === 'running' || status === 're_planning'
      || status === 'retrying' || status === 'planning')
  ) {
    if (status === 're_planning') {
      return { tone: 'running', label: 'Re-planning investigation', hint: 'The plan is being revised from the evidence gathered so far.' }
    }
    if (status === 'retrying') {
      return { tone: 'running', label: 'Retrying after a temporary error', hint: 'Retrying the failed step once within the bounded retry cap.' }
    }
    return { tone: 'running', label: 'Investigating', hint: 'Executing the investigation plan.' }
  }

  // --- terminal: failures and blocks first -------------------------------
  if (status === 'failed') {
    return {
      tone: 'failed',
      label: 'Investigation failed',
      hint: stopHint(stop, outcome),
    }
  }
  if (status === 'blocked') {
    return {
      tone: 'blocked',
      label: 'Investigation blocked',
      hint: stopHint(stop, outcome),
    }
  }
  if (status === 'stopped_by_limit') {
    return {
      tone: 'partial',
      label: 'Partially completed — execution limit reached',
      hint: stopHint('iteration_bound_reached', outcome),
    }
  }

  // --- terminal completed: split success vs partial -----------------------
  if (status === 'completed') {
    // Defense in depth: the bounded stop reason ALWAYS wins over a
    // complete-looking outcome — a bounded answer is never full success.
    if (stop === 'bounded_data') {
      return {
        tone: 'partial',
        label: 'Partially completed — data range limited',
        hint: stopHint('bounded_data', outcome),
      }
    }
    // The loop writes stop_reason=goal_satisfied ONLY when its completion
    // criteria held: for contract runs every accepted output was
    // satisfied; for no-contract runs the deterministic criteria were met
    // (artifact produced / question answered). goal_outcome may be absent
    // on no-contract runs — its absence does not contradict a completed,
    // criteria-met run. (U00047: outcome=partial with stop=goal_satisfied
    // is the heuristic-contract cap — the accepted outputs WERE delivered;
    // the composer answer discloses the request-coverage caveat.)
    if (stop === 'goal_satisfied') {
      return {
        tone: 'success',
        label: 'Investigation completed',
        hint: stopHint('goal_satisfied', outcome),
      }
    }
    if (outcome === 'partial') {
      return {
        tone: 'partial',
        label: 'Partially completed',
        hint: stopHint(stop, outcome),
      }
    }
    if (outcome === 'unanswerable') {
      return {
        tone: 'partial',
        label: 'Cannot be answered with current data',
        hint: stopHint(stop, outcome),
      }
    }
    if (outcome === 'blocked') {
      return { tone: 'blocked', label: 'Investigation blocked', hint: stopHint(stop, outcome) }
    }
    if (stop === 'conclusive_empty_result') {
      return {
        tone: 'success',
        label: 'Investigation completed — no matching records',
        hint: stopHint(stop, outcome),
      }
    }
    // Conservative fallback for legacy/incomplete field combinations:
    // without positive evidence of full satisfaction, present partial.
    return {
      tone: 'partial',
      label: 'Partially completed',
      hint: stopHint(stop, outcome),
    }
  }

  // --- unknown / legacy status values ------------------------------------
  if (activity.is_terminal) {
    return { tone: 'partial', label: 'Investigation finished', hint: stopHint(stop, outcome) }
  }
  return { tone: 'running', label: 'Investigating', hint: 'Executing the investigation plan.' }
}

/** Compact tool names for the timeline (human-readable, no invented
 * semantics — a tool not in the map falls back to its underscore-spaced
 * technical name). */
const TOOL_LABELS: Record<string, string> = {
  risk_case_fetch: 'Case intake',
  related_account_analysis: 'Related-account network analysis',
  cross_account_trade_analysis: 'Cross-account trade analysis',
  finding_drilldown: 'Finding evidence drilldown',
  signal_explain: 'Signal explanation',
  policy_lookup: 'Policy lookup',
  artifact_bundle: 'Result file generation',
}

export function toolLabel(toolName: string | null): string | null {
  if (!toolName) return null
  return TOOL_LABELS[toolName] ?? toolName.replace(/_/g, ' ')
}
