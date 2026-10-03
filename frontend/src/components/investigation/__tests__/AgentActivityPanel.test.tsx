/**
 * Agent Activity V1 — deterministic component tests
 * (docs/baselines/2026-09-19-agent-activity-v1.md §16).
 *
 * Renders the bounded UI-safe projection with scripted payloads and pins
 * the contract: goal/status/iteration visible; plan step states; tool
 * rows; decision rows; four distinct terminals; retry; re-plan
 * supersession; evidence/artifact refs; NO chain-of-thought; NO raw
 * transaction payloads; duplicate/out-of-order normalization; terminal
 * polling stop (hook test).
 */

import { describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import {
  AgentActivityPanel,
  eventTitle,
  stepState,
} from '../AgentActivityPanel'
import { InvestigationApiError } from '@/api/investigation'
import { normalizeActivity, useAgentActivity } from '@/hooks/useAgentActivity'
import { renderHook } from '@testing-library/react'
import type { AgentActivityRecord, TaskActivityResponse } from '@/api/types'

function record(seq: number, event_type: string, extra: Partial<AgentActivityRecord> = {}): AgentActivityRecord {
  return {
    seq,
    event_type,
    timestamp: `2026-09-19T10:00:${String(seq % 60).padStart(2, '0')}Z`,
    iteration: 1,
    step_id: 'S1',
    step_type: 'inspect_timeline',
    tool_name: null,
    outcome: null,
    decision: null,
    agent_status: null,
    stop_reason: null,
    summary: null,
    artifact_ids: [],
    ...extra,
  }
}

function activityResponse(overrides: Partial<TaskActivityResponse> = {}): TaskActivityResponse {
  return {
    task_id: 'TASK-1',
    investigation_id: 'INV-1',
    case_id: 'U00299',
    goal: 'find candidate cross-account opposite trades',
    criteria_kind: 'question_answered',
    focused_finding_id: 'F2',
    agent_status: 'running',
    stop_reason: null,
    iteration: 2,
    max_iterations: 8,
    plan_revisions: 1,
    plan_steps: [
      { step_id: 'S1', type: 'analyze_cross_account_trades', status: 'success', tool_name: 'cross_account_trade_analysis' },
      { step_id: 'S2', type: 'inspect_evidence', status: 'running', tool_name: 'finding_drilldown' },
      { step_id: 'S3', type: 'retrieve_policy', status: 'pending', tool_name: 'policy_lookup' },
    ],
    artifact_ids: [],
    activity: [],
    is_terminal: false,
    ...overrides,
  }
}

describe('AgentActivityPanel', () => {
  it('shows goal, focus, status, and iteration in the run header', () => {
    render(<AgentActivityPanel activity={activityResponse()} />)
    expect(screen.getByTestId('agent-goal')).toHaveTextContent(
      'find candidate cross-account opposite trades')
    expect(screen.getByText('finding F2')).toBeInTheDocument()
    expect(screen.getByText('U00299')).toBeInTheDocument()
    expect(screen.getByTestId('agent-iteration')).toHaveTextContent(
      'Iteration 2 of 8')
    expect(screen.getByTestId('agent-status')).toHaveTextContent(
      'Investigating')
  })

  it('renders plan step states: completed, current, pending', () => {
    render(<AgentActivityPanel activity={activityResponse()} />)
    const plan = screen.getByTestId('agent-plan')
    expect(plan).toHaveTextContent('analyze cross account trades')
    expect(plan).toHaveTextContent('✓')
    expect(plan).toHaveTextContent('●')
    expect(plan).toHaveTextContent('○')
  })

  it('renders superseded steps after a re-plan', () => {
    const activity = activityResponse({
      plan_revisions: 2,
      plan_steps: [
        { step_id: 'S2', type: 'cross_account_analysis', status: 'superseded', tool_name: null },
        { step_id: 'S4', type: 'retrieve_policy', status: 'pending', tool_name: null },
      ],
    })
    render(<AgentActivityPanel activity={activity} />)
    const plan = screen.getByTestId('agent-plan')
    expect(plan).toHaveTextContent('Plan 2')
    expect(plan).toHaveTextContent('(re-planned)')
    expect(plan).toHaveTextContent('↷')
    expect(plan.textContent).toContain('superseded')
  })

  it('renders tool rows with human capability names and summaries', () => {
    const activity = activityResponse({
      activity: [
        record(1, 'tool.called', { tool_name: 'cross_account_trade_analysis' }),
        record(2, 'observation.recorded', {
          tool_name: 'cross_account_trade_analysis',
          summary: 'Found 3 candidate relationships',
          outcome: 'success',
        }),
      ],
    })
    render(<AgentActivityPanel activity={activity} />)
    expect(screen.getByText('Invoking capability')).toBeInTheDocument()
    // the capability appears in the plan snapshot AND the tool row
    expect(
      screen.getAllByText('cross account trade analysis').length,
    ).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Found 3 candidate relationships')).toBeInTheDocument()
  })

  it('renders decision rows with decision chip and user-safe reason', () => {
    const activity = activityResponse({
      activity: [
        record(1, 'decision.made', { decision: 'CONTINUE', summary: 'more scope remains' }),
        record(2, 'decision.made', { decision: 'BLOCK', summary: 'required evidence is unavailable' }),
      ],
    })
    render(<AgentActivityPanel activity={activity} />)
    expect(screen.getAllByTestId('agent-decision')).toHaveLength(2)
    expect(screen.getByText('CONTINUE')).toBeInTheDocument()
    expect(screen.getByText('BLOCK')).toBeInTheDocument()
    expect(screen.getByText('required evidence is unavailable')).toBeInTheDocument()
  })

  it('renders the four terminal states distinctly', () => {
    const terminals: Array<[string, string]> = [
      ['completed', 'Investigation completed'],
      ['blocked', 'Investigation blocked'],
      ['failed', 'Investigation failed'],
      ['stopped_by_limit', 'Stopped because the execution limit was reached'],
    ]
    for (const [status, label] of terminals) {
      const { unmount } = render(
        <AgentActivityPanel
          activity={activityResponse({ agent_status: status, is_terminal: true })}
        />,
      )
      const badge = screen.getByTestId('agent-status')
      expect(badge).toHaveTextContent(label)
      expect(badge).toHaveAttribute('data-agent-status', status)
      unmount()
    }
  })

  it('renders a retry activity row', () => {
    const activity = activityResponse({
      activity: [record(1, 'step.retried', {
        tool_name: 'risk_case_fetch',
        summary: 'transient failure; retrying once',
      })],
    })
    render(<AgentActivityPanel activity={activity} />)
    expect(screen.getByText('Retrying after a temporary error')).toBeInTheDocument()
    expect(screen.getByText('transient failure; retrying once')).toBeInTheDocument()
  })

  it('shows Starting… instead of Iteration 0 (QA-08)', () => {
    const activity = activityResponse({ iteration: 0 })
    render(<AgentActivityPanel activity={activity} />)
    expect(screen.getByTestId('agent-iteration')).toHaveTextContent('Starting…')
    expect(screen.queryByText(/Iteration 0/)).toBeNull()
  })

  it('shows user-facing display summaries when present (QA-05)', () => {
    const activity = activityResponse({
      activity: [record(1, 'observation.recorded', {
        tool_name: 'cross_account_trade_analysis',
        summary: 'Found 3 candidate cross-account opposite-trade relationships (candidates only, not conclusions).',
      })],
    })
    render(<AgentActivityPanel activity={activity} />)
    expect(screen.getByText(/candidates only, not conclusions/)).toBeInTheDocument()
  })

  it('renders the blocked/dependency state distinctly (QA-04)', () => {
    const activity = activityResponse({
      agent_status: 'blocked',
      stop_reason: 'dependency_unavailable',
      is_terminal: true,
    })
    render(<AgentActivityPanel activity={activity} />)
    const badge = screen.getByTestId('agent-status')
    expect(badge).toHaveTextContent('Investigation blocked')
    expect(badge).toHaveAttribute('data-agent-status', 'blocked')
    expect(badge).toHaveTextContent('dependency unavailable')
  })

  it('renders evidence/artifact references', () => {
    const activity = activityResponse({
      artifact_ids: ['ART-abc123'],
      activity: [record(1, 'observation.recorded', {
        artifact_ids: ['ART-abc123'],
      })],
    })
    render(<AgentActivityPanel activity={activity} />)
    expect(screen.getAllByTestId('agent-artifact-ref').length).toBeGreaterThan(0)
    expect(screen.getAllByText('ART-abc123').length).toBeGreaterThan(0)
  })

  it('NEVER renders chain-of-thought or raw transaction payloads', () => {
    const activity = activityResponse({
      activity: [
        record(1, 'observation.recorded', {
          tool_name: 'finding_drilldown',
          summary: 'timeline with 12 events',
        }),
      ],
    })
    const { container } = render(<AgentActivityPanel activity={activity} />)
    const text = container.textContent ?? ''
    // forbidden content classes (the projection cannot carry them, and the
    // component adds nothing of the kind)
    for (const forbidden of [
      'chain of thought', 'chain-of-thought', 'reasoning tokens',
      'transaction_evidence', 'risk_reason', '"price"',
      'system prompt', 'model thinking',
    ]) {
      expect(text.toLowerCase()).not.toContain(forbidden)
    }
    // no raw JSON blobs rendered
    expect(container.querySelector('pre')).toBeNull()
  })

  it('normalizes duplicate and out-of-order records deterministically', () => {
    const rows = [
      record(3, 'decision.made'),
      record(1, 'agent.started'),
      record(3, 'decision.made'),        // duplicate
      record(2, 'observation.recorded'),
    ]
    const normalized = normalizeActivity(rows)
    expect(normalized.map((r) => r.seq)).toEqual([1, 2, 3])
    const activity = activityResponse({ activity: rows })
    render(<AgentActivityPanel activity={activity} />)
    const items = screen.getAllByTestId('agent-activity-item')
    expect(items.map((i) => i.getAttribute('data-seq'))).toEqual(['1', '2', '3'])
  })

  it('shows an empty-state message when no loop run exists', () => {
    const activity = activityResponse({ activity: [], plan_steps: [] })
    render(<AgentActivityPanel activity={activity} />)
    // renders the header but an empty timeline (no crash, no fabricated rows)
    expect(screen.getByTestId('agent-goal')).toBeInTheDocument()
    expect(screen.queryAllByTestId('agent-activity-item')).toHaveLength(0)
  })
})

describe('label helpers', () => {
  it('maps event types to human-readable titles', () => {
    expect(eventTitle('agent.started')).toBe('Agent run started')
    expect(eventTitle('decision.made')).toBe('Decision')
    expect(eventTitle('agent.stopped')).toBe(
      'Investigation stopped (execution limit)')
    expect(eventTitle('custom.event')).toBe('custom event')
  })

  it('maps plan step statuses to markers', () => {
    expect(stepState('success').marker).toBe('✓')
    expect(stepState('running').marker).toBe('●')
    expect(stepState('pending').marker).toBe('○')
    expect(stepState('superseded').marker).toBe('↷')
  })
})

describe('useAgentActivity polling', () => {
  it('stops polling at a terminal state and deduplicates records', async () => {
    const terminal = activityResponse({
      agent_status: 'completed', is_terminal: true,
      activity: [record(1, 'agent.started'), record(1, 'agent.started')],
    })
    const apiModule = await import('@/api/investigation')
    const spy = vi
      .spyOn(apiModule.investigationApi, 'getTaskActivity')
      .mockResolvedValue(terminal)

    const { result } = renderHook(() =>
      useAgentActivity('TASK-1', { poll: true }))
    await waitFor(() => expect(result.current.activity).not.toBeNull())
    // terminal: exactly ONE fetch, no further polling
    await new Promise((r) => setTimeout(r, 2500))
    expect(spy).toHaveBeenCalledTimes(1)
    // the raw snapshot carries what the backend served; the panel-side
    // normalizeActivity deduplicates it
    expect(result.current.activity?.activity).toHaveLength(2)
    expect(normalizeActivity(result.current.activity!.activity)).toHaveLength(1)
    spy.mockRestore()
  })

  it('QA-03: switching task id resets activity and handles 404', async () => {
    const apiModule = await import('@/api/investigation')
    const spy = vi
      .spyOn(apiModule.investigationApi, 'getTaskActivity')
      .mockResolvedValueOnce(activityResponse({ task_id: 'TASK-A' }))
      .mockRejectedValueOnce(new InvestigationApiError(
        'No Agent Activity', 404, 'NO_AGENT_ACTIVITY', '/tasks/TASK-B/activity'))

    const { result, rerender } = renderHook(
      ({ id }) => useAgentActivity(id, { poll: false }),
      { initialProps: { id: 'TASK-A' } })
    await waitFor(() => expect(result.current.activity?.task_id).toBe('TASK-A'))

    // switch to investigation B's task: stale A data must clear, 404 →
    // notFound fallback (no A activity under B)
    rerender({ id: 'TASK-B' })
    await waitFor(() => expect(result.current.notFound).toBe(true))
    expect(result.current.activity).toBeNull()
    spy.mockRestore()
  })

  it('QA-03: stale polling callback from A never updates B', async () => {
    const apiModule = await import('@/api/investigation')
    let resolveA: (v: TaskActivityResponse) => void = () => {}
    const slowA = new Promise<TaskActivityResponse>((r) => { resolveA = r })
    const spy = vi
      .spyOn(apiModule.investigationApi, 'getTaskActivity')
      .mockImplementationOnce(() => slowA)          // A: hangs
      .mockResolvedValue(activityResponse({ task_id: 'TASK-B' }))

    const { result, rerender } = renderHook(
      ({ id }) => useAgentActivity(id, { poll: false }),
      { initialProps: { id: 'TASK-A' } })
    rerender({ id: 'TASK-B' })
    await waitFor(() => expect(result.current.activity?.task_id).toBe('TASK-B'))

    // A's slow response lands AFTER B is active — must be ignored
    resolveA(activityResponse({ task_id: 'TASK-A' }))
    await new Promise((r) => setTimeout(r, 50))
    expect(result.current.activity?.task_id).toBe('TASK-B')
    spy.mockRestore()
  })

  it('AL-2.5: an early 404 (run not yet persisted) does not permanently kill polling', async () => {
    // Race found by the real-RP browser E2E: the first poll can precede
    // the loop thread's first state persistence. The hook must retry for
    // a bounded grace window instead of showing "No Agent Loop run yet"
    // forever while the run is actually executing.
    const apiModule = await import('@/api/investigation')
    const notFoundErr = new InvestigationApiError(
      'No Agent Activity', 404, 'NO_AGENT_ACTIVITY',
      '/tasks/TASK-R/activity')
    const terminal = activityResponse({
      task_id: 'TASK-R', agent_status: 'completed', is_terminal: true,
      activity: [record(1, 'agent.started')],
    })
    const spy = vi
      .spyOn(apiModule.investigationApi, 'getTaskActivity')
      .mockRejectedValueOnce(notFoundErr)
      .mockRejectedValueOnce(notFoundErr)
      .mockResolvedValueOnce(terminal)

    const { result } = renderHook(() =>
      useAgentActivity('TASK-R', { poll: true }))
    await waitFor(
      () => expect(result.current.activity?.task_id).toBe('TASK-R'),
      { timeout: 8000 })
    // recovered: the transient 404s resolved into the real snapshot
    expect(result.current.notFound).toBe(false)
    expect(spy).toHaveBeenCalledTimes(3)
    spy.mockRestore()
  })
})
