/**
 * AgentActivityBlock — conversation-inline Activity tests.
 *
 * Pins (a) the status-presentation mapping contract (agent_status ×
 * stop_reason × goal_outcome → one presentation, with the partial/
 * bounded semantics that must never render as full success), (b) the
 * block's rendering contract (summary row from REAL fields, collapsed-
 * by-default history, seq-ordered timeline, artifact ids, bounded
 * summaries), and (c) the live auto-expand/auto-collapse behavior.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AgentActivityBlock } from '../AgentActivityBlock'
import {
  resolveActivityPresentation,
  toolLabel,
} from '../activityPresentation'
import { investigationApi } from '@/api/investigation'
import type { AgentActivityRecord, TaskActivityResponse } from '@/api/types'

// vitest runs WITHOUT globals here (no test block in vite.config.ts), so
// RTL does not auto-cleanup — unmounting and spy restoration must be
// explicit or earlier tests' snapshots/polling leak into later ones.
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

function record(
  seq: number,
  event_type: string,
  extra: Partial<AgentActivityRecord> = {},
): AgentActivityRecord {
  return {
    seq,
    event_type,
    timestamp: '2026-10-18T10:00:00Z',
    iteration: 1,
    step_id: 'S1',
    step_type: 'analyze_related_account_set_trades',
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

function activityResponse(
  overrides: Partial<TaskActivityResponse> = {},
): TaskActivityResponse {
  return {
    task_id: 'TASK-B',
    investigation_id: 'INV-1',
    case_id: 'U00047',
    goal: 'hedge analysis with file export',
    criteria_kind: 'artifact_produced',
    focused_finding_id: null,
    agent_status: 'completed',
    stop_reason: 'goal_satisfied',
    iteration: 3,
    max_iterations: 8,
    plan_revisions: 1,
    plan_steps: [
      {
        step_id: 'S1',
        type: 'analyze_related_account_set_trades',
        status: 'success',
        tool_name: 'cross_account_trade_analysis',
      },
    ],
    artifact_ids: ['ART-abc123'],
    activity: [
      record(1, 'agent.started'),
      record(2, 'tool.called', { tool_name: 'cross_account_trade_analysis' }),
      record(3, 'observation.recorded', {
        tool_name: 'cross_account_trade_analysis',
        outcome: 'success',
        summary: 'Found 5 candidate cross-account opposite-trade relationships',
      }),
      record(4, 'agent.completed', { agent_status: 'completed' }),
    ],
    is_terminal: true,
    goal_outcome: 'complete',
    ...overrides,
  }
}

function mockActivity(response: TaskActivityResponse) {
  return vi
    .spyOn(investigationApi, 'getTaskActivity')
    .mockResolvedValue(response)
}

// ---------------------------------------------------------------------------
// Status mapping (the correctness contract)
// ---------------------------------------------------------------------------

describe('resolveActivityPresentation', () => {
  it('maps completed + complete + goal_satisfied to success', () => {
    const p = resolveActivityPresentation({
      agent_status: 'completed',
      stop_reason: 'goal_satisfied',
      goal_outcome: 'complete',
      is_terminal: true,
    })
    expect(p.tone).toBe('success')
    expect(p.label).toBe('Investigation completed')
  })

  it('NEVER maps bounded_data to success — even against a complete outcome', () => {
    const p = resolveActivityPresentation({
      agent_status: 'completed',
      stop_reason: 'bounded_data',
      goal_outcome: 'partial',
      is_terminal: true,
    })
    expect(p.tone).toBe('partial')
    expect(p.label).toMatch(/Partially completed/)
  })

  it('maps goal_satisfied stop to success even when the heuristic cap made the outcome partial', () => {
    // heuristic-contract runs cap COMPLETE at PARTIAL even when every
    // accepted output was satisfied — the loop verified delivery, so the
    // stop label (not the capped outcome) drives the presentation
    const p = resolveActivityPresentation({
      agent_status: 'completed',
      stop_reason: 'goal_satisfied',
      goal_outcome: 'partial',
      is_terminal: true,
    })
    expect(p.tone).toBe('success')
  })

  it('maps outcome=partial with a non-goal_satisfied stop to partial', () => {
    const p = resolveActivityPresentation({
      agent_status: 'completed',
      stop_reason: 'replan_failure',
      goal_outcome: 'partial',
      is_terminal: true,
    })
    expect(p.tone).toBe('partial')
  })

  it('maps completed + goal_satisfied + absent outcome to success (no-contract criteria run)', () => {
    const p = resolveActivityPresentation({
      agent_status: 'completed',
      stop_reason: 'goal_satisfied',
      goal_outcome: null,
      is_terminal: true,
    })
    expect(p.tone).toBe('success')
  })

  it('maps a partial outcome with a missing stop reason to partial (conservative)', () => {
    const p = resolveActivityPresentation({
      agent_status: 'completed',
      stop_reason: null,
      goal_outcome: 'partial',
      is_terminal: true,
    })
    expect(p.tone).toBe('partial')
  })

  it('maps stopped_by_limit to a bounded partial — never success', () => {
    const p = resolveActivityPresentation({
      agent_status: 'stopped_by_limit',
      stop_reason: 'iteration_bound_reached',
      goal_outcome: 'partial',
      is_terminal: true,
    })
    expect(p.tone).toBe('partial')
    expect(p.label).toMatch(/execution limit/)
  })

  it('maps failed to failed', () => {
    const p = resolveActivityPresentation({
      agent_status: 'failed',
      stop_reason: 'planning_failure',
      goal_outcome: null,
      is_terminal: true,
    })
    expect(p.tone).toBe('failed')
    expect(p.label).toBe('Investigation failed')
  })

  it('maps blocked to blocked', () => {
    const p = resolveActivityPresentation({
      agent_status: 'blocked',
      stop_reason: null,
      goal_outcome: 'blocked',
      is_terminal: true,
    })
    expect(p.tone).toBe('blocked')
  })

  it('keeps running/re_planning/retrying non-terminal states running', () => {
    for (const status of ['running', 're_planning', 'retrying']) {
      const p = resolveActivityPresentation({
        agent_status: status,
        stop_reason: null,
        goal_outcome: null,
        is_terminal: false,
      })
      expect(p.tone).toBe('running')
    }
  })

  it('treats unanswerable as a partial family state, never success', () => {
    const p = resolveActivityPresentation({
      agent_status: 'completed',
      stop_reason: 'replan_failure',
      goal_outcome: 'unanswerable',
      is_terminal: true,
    })
    expect(p.tone).toBe('partial')
  })
})

describe('toolLabel', () => {
  it('humanizes known capability names', () => {
    expect(toolLabel('cross_account_trade_analysis')).toBe(
      'Cross-account trade analysis')
    expect(toolLabel('artifact_bundle')).toBe('Result file generation')
  })
  it('falls back to the spaced technical name', () => {
    expect(toolLabel('some_new_tool')).toBe('some new tool')
    expect(toolLabel(null)).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Component rendering
// ---------------------------------------------------------------------------

describe('AgentActivityBlock', () => {
  it('renders the summary row from real fields (status, capability calls, artifacts)', async () => {
    mockActivity(activityResponse())
    render(<AgentActivityBlock taskId="TASK-B" />)
    const status = await screen.findByTestId('activity-block-status')
    expect(status).toHaveTextContent('Investigation completed')
    expect(status).toHaveAttribute('data-tone', 'success')
    const summary = screen.getByTestId('activity-block-summary')
    // real counts derived from records/artifact_ids — no invented numbers
    expect(summary).toHaveTextContent('1 capability call')
    expect(summary).toHaveTextContent('1 artifact')
  })

  it('renders partial (bounded data) as partial — never as success', async () => {
    mockActivity(activityResponse({
      agent_status: 'completed',
      stop_reason: 'bounded_data',
      goal_outcome: 'partial',
      activity: [
        record(1, 'agent.started'),
        record(2, 'agent.completed'),
      ],
    }))
    render(<AgentActivityBlock taskId="TASK-B" defaultOpen />)
    const status = await screen.findByTestId('activity-block-status')
    expect(status).toHaveAttribute('data-tone', 'partial')
    expect(status).toHaveTextContent('Partially completed')
    expect(screen.getByTestId('activity-block-hint')).toHaveTextContent(
      'part of the requested scope is not available')
  })

  it('renders a historical run collapsed by default; expanding shows the '
    + 'seq-ordered timeline, humanized tool names and artifact ids', async () => {
    const spy = mockActivity(activityResponse())
    render(<AgentActivityBlock taskId="TASK-B" />)
    const block = await screen.findByTestId('agent-activity-block')
    expect(block).toHaveAttribute('data-tone', 'success')
    expect(screen.queryByTestId('activity-block-timeline')).toBeNull()
    // read-only historical fetch: exactly ONE call, no polling
    expect(spy).toHaveBeenCalledTimes(1)

    fireEvent.click(screen.getByRole('button'))
    const timeline = screen.getByTestId('activity-block-timeline')
    const items = timeline.querySelectorAll(
      '[data-testid="activity-block-item"]')
    expect(items).toHaveLength(4)
    expect(items[0].getAttribute('data-seq')).toBe('1')
    expect(items[3].getAttribute('data-seq')).toBe('4')
    expect(timeline).toHaveTextContent('Cross-account trade analysis')
    expect(timeline).toHaveTextContent(
      'Found 5 candidate cross-account opposite-trade relationships')
    expect(screen.getByTestId('activity-block-artifacts')).toHaveTextContent(
      'ART-abc123')
  })

  it('auto-expands while a live run executes and auto-collapses at the '
    + 'terminal state', async () => {
    const responses = [
        activityResponse({
          agent_status: 'running', stop_reason: null,
          goal_outcome: null, is_terminal: false, iteration: 1,
          artifact_ids: [], activity: [record(1, 'agent.started')],
        }),
        activityResponse(),
      ]
      vi.spyOn(investigationApi, 'getTaskActivity').mockImplementation(
        () => Promise.resolve(responses.shift() ?? activityResponse()))
      render(<AgentActivityBlock taskId="TASK-B" live />)
      // first fetch (immediate): live + running → auto-expanded
      expect(
        await screen.findByTestId('activity-block-timeline'))
        .toBeInTheDocument()
      expect(screen.getByTestId('activity-block-status')).toHaveTextContent(
        'Investigating')
      // next poll (2s interval) fetches the terminal snapshot → collapsed
      await waitFor(
        () => expect(screen.queryByTestId('activity-block-timeline'))
          .toBeNull(),
        { timeout: 5000 })
      expect(screen.getByTestId('activity-block-status')).toHaveTextContent(
        'Investigation completed')
  })

  it('reports a persistent activity error without implying the agent '
    + 'is still working', async () => {
    vi.spyOn(investigationApi, 'getTaskActivity').mockRejectedValue(
      new Error('backend unreachable'))
    render(<AgentActivityBlock taskId="TASK-B" />)
    const err = await screen.findByTestId('activity-block-error')
    expect(err).toHaveTextContent('temporarily unavailable')
    expect(screen.queryByTestId('activity-block-status')).toBeNull()
  })

  it('truncates oversized summaries (bounded display, no raw dumps)', async () => {
    mockActivity(activityResponse({
      activity: [
        record(1, 'observation.recorded', { summary: 'x'.repeat(500) }),
      ],
    }))
    render(<AgentActivityBlock taskId="TASK-B" defaultOpen />)
    const timeline = await screen.findByTestId('activity-block-timeline')
    const text = timeline.textContent ?? ''
    expect(text.length).toBeLessThan(1000)
  })
})

describe('AgentActivityBlock bounded timeline', () => {
  it('caps very long timelines at the latest 60 events with a disclosure', async () => {
    const many = Array.from({ length: 130 }, (_, i) =>
      record(i + 1, 'tool.completed', {
        tool_name: 'cross_account_trade_analysis',
        outcome: 'success',
      }))
    mockActivity(activityResponse({ activity: many }))
    render(<AgentActivityBlock taskId="TASK-B" defaultOpen />)
    const timeline = await screen.findByTestId('activity-block-timeline')
    expect(
      timeline.querySelectorAll('[data-testid="activity-block-item"]'),
    ).toHaveLength(60)
    // the cap is disclosed, never silent
    expect(
      screen.getByTestId('activity-block-omitted'),
    ).toHaveTextContent('Showing the latest 60 of 130 events')
    // the retained rows are the LATEST ones (seq 71..130)
    const first = timeline.querySelector('[data-testid="activity-block-item"]')
    expect(first?.getAttribute('data-seq')).toBe('71')
    const last = timeline.querySelectorAll('[data-testid="activity-block-item"]')
    expect(last[last.length - 1]?.getAttribute('data-seq')).toBe('130')
  })

  it('renders short timelines without the disclosure', async () => {
    mockActivity(activityResponse())
    render(<AgentActivityBlock taskId="TASK-B" defaultOpen />)
    await screen.findByTestId('activity-block-timeline')
    expect(screen.queryByTestId('activity-block-omitted')).toBeNull()
  })
})
