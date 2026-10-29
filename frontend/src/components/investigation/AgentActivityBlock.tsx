/**
 * AgentActivityBlock — the conversation-inline Agent Activity block
 * (Activity-in-conversation redesign).
 *
 * A compact, low-interference block rendered INSIDE the conversation
 * main line, between the user's investigation request and the Agent's
 * final answer. Collapsed by default after the run reaches its terminal
 * state (the summary row stays in the conversation); auto-expanded with
 * live progress while the run is executing.
 *
 * Data source: the SAME bounded UI-safe projection the right-hand
 * Activity panel uses — GET /tasks/{task_id}/activity via
 * useAgentActivity (seq-ordered, deduplicated; polling stops at the
 * terminal status). This component NEVER renders chain-of-thought,
 * internal reasoning, raw tool payloads, or full parameter objects —
 * the projection excludes them by backend contract.
 *
 * Status presentation comes exclusively from resolveActivityPresentation
 * (agent_status × stop_reason × goal_outcome) — never inferred from a
 * frontend boolean or from the mere presence of artifacts.
 */

import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import {
  CheckCircle2,
  ChevronDown,
  CircleAlert,
  CircleDashed,
  FileText,
  Loader2,
  OctagonX,
  ShieldAlert,
  Wrench,
} from 'lucide-react'
import type { ComponentType } from 'react'
import { useEffect, useRef, useState } from 'react'
import type { AgentActivityRecord } from '@/api/types'
import { useAgentActivity } from '@/hooks/useAgentActivity'
import {
  resolveActivityPresentation,
  toolLabel,
} from './activityPresentation'

const TONE_ICON: Record<string, ComponentType<{ className?: string }>> = {
  running: Loader2,
  success: CheckCircle2,
  partial: CircleDashed,
  failed: OctagonX,
  blocked: ShieldAlert,
}

const TONE_CLASS: Record<string, string> = {
  running: 'text-blue-700',
  success: 'text-emerald-700',
  partial: 'text-amber-700',
  failed: 'text-red-700',
  blocked: 'text-orange-700',
}

/** Human-readable timeline titles (same contract as the panel view). */
const EVENT_TITLES: Record<string, string> = {
  'agent.started': 'Agent run started',
  'context.resolved': 'Investigation context resolved',
  'plan.created': 'Plan created',
  'step.started': 'Step started',
  'tool.called': 'Invoking capability',
  'tool.completed': 'Capability returned',
  'observation.recorded': 'Result summary',
  'decision.made': 'Decision',
  'step.completed': 'Step completed',
  'step.retried': 'Retrying after a temporary error',
  'agent.completed': 'Investigation completed',
  'agent.blocked': 'Investigation blocked',
  'agent.stopped': 'Investigation stopped (execution limit)',
  'agent.failed': 'Investigation failed',
}

function eventTitle(eventType: string): string {
  return EVENT_TITLES[eventType] ?? eventType.replace(/[._]/g, ' ')
}

/** Outcome badge tone: failures are distinguishable, everything else
 * stays neutral (the row title already carries the state). */
function outcomeTone(outcome: string): 'destructive' | 'secondary' {
  return outcome === 'execution_error' || outcome === 'integration_error'
    || outcome === 'validation_error'
    ? 'destructive'
    : 'secondary'
}

function TimelineRow({ record }: { record: AgentActivityRecord }) {
  const title = eventTitle(record.event_type)
  const label = toolLabel(record.tool_name)
  const summary = record.summary ?? ''
  // Bounded display: long observation summaries (e.g. result counting)
  // are truncated to keep the timeline scannable; the full answer and
  // the Artifacts carry the complete content.
  const truncated = summary.length > 220 ? `${summary.slice(0, 220)}…` : summary
  const isTerminalEvent = record.event_type.startsWith('agent.')
  return (
    <li
      className={isTerminalEvent
        ? 'space-y-0.5 border-l-2 border-primary/40 pl-3'
        : 'space-y-0.5 border-l-2 border-border pl-3'}
      data-testid="activity-block-item"
      data-seq={record.seq}
      data-event-type={record.event_type}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <span className="text-xs font-medium">{title}</span>
        {record.tool_name && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Wrench className="h-3 w-3" aria-hidden />
            <span className="max-w-[220px] truncate" title={record.tool_name}>
              {label}
            </span>
          </span>
        )}
        {record.outcome && (
          <Badge
            variant={outcomeTone(record.outcome)}
            className="px-1.5 py-0 text-[10px]"
            data-testid="activity-block-outcome"
          >
            {record.outcome.replace(/_/g, ' ')}
          </Badge>
        )}
      </div>
      {truncated && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {truncated}
        </p>
      )}
    </li>
  )
}

function BlockError({ message }: { message: string }) {
  return (
    <div
      className="flex items-center gap-1.5 px-1 text-xs text-muted-foreground"
      data-testid="activity-block-error"
    >
      <CircleAlert className="h-3.5 w-3.5" aria-hidden />
      <span>Activity status temporarily unavailable — {message}</span>
    </div>
  )
}

export interface AgentActivityBlockProps {
  taskId: string
  /** Live runs poll (2s, terminal-stopping — the shared useAgentActivity
   * contract); historical turns fetch once and render read-only. */
  live?: boolean
  /** Open while running, collapsed once terminal (unless the user has
   * explicitly toggled it — their choice wins afterwards). */
  defaultOpen?: boolean
}

export function AgentActivityBlock({
  taskId,
  live = false,
  defaultOpen,
}: AgentActivityBlockProps) {
  const { activity, loading, error, notFound } = useAgentActivity(taskId, {
    poll: live,
  })
  // Auto-open while running; auto-collapse at the terminal state — but a
  // manual toggle by the user always wins afterwards (their explicit
  // choice is not overridden by later snapshots).
  const [open, setOpen] = useState<boolean | null>(null)
  const userToggled = useRef(false)
  const wasRunning = useRef(false)
  useEffect(() => {
    if (userToggled.current) return
    const running = !!activity && !activity.is_terminal
    if (running) {
      wasRunning.current = true
      setOpen(defaultOpen ?? true)
    } else if (activity?.is_terminal && wasRunning.current) {
      setOpen(defaultOpen ?? false)
    }
  }, [activity, defaultOpen])

  if (notFound) {
    // Non-loop tasks never render a block (the caller only mounts this
    // for agent_loop turns); a loop task whose projection is missing is
    // surfaced honestly rather than silently hidden.
    return (
      <div
        className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground"
        data-testid="activity-block-missing"
      >
        No execution record is available for this run.
      </div>
    )
  }

  if (!activity) {
    // Distinguish "reading the record" (transient fetch) from
    // "temporarily unavailable" (persistent error) — neither implies the
    // Agent is still working.
    if (error) {
      return <BlockError message={error} />
    }
    return (
      <div
        className="flex items-center gap-2 rounded-md border px-3 py-2"
        data-testid="activity-block-loading"
        role="status"
        aria-label="Loading execution record"
      >
        <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
        <span className="text-xs text-muted-foreground">
          Reading execution record…
        </span>
        {loading && <Skeleton className="ml-auto h-3 w-24" aria-hidden />}
      </div>
    )
  }

  const presentation = resolveActivityPresentation(activity)
  const Icon = TONE_ICON[presentation.tone] ?? CircleDashed
  const recordsAll = [...activity.activity].sort((a, b) => a.seq - b.seq)
  // Bounded timeline display: very long runs render the LATEST events
  // only (a real 130-event run took ~20s to mount in full and pushed the
  // conversation layout); the complete trace stays available in the
  // Activity tab. The cap is disclosed, never silent.
  const MAX_TIMELINE_ROWS = 60
  const omitted = Math.max(0, recordsAll.length - MAX_TIMELINE_ROWS)
  const records = omitted > 0 ? recordsAll.slice(-MAX_TIMELINE_ROWS) : recordsAll
  const stepCount = recordsAll.filter((r) => r.event_type === 'step.completed'
    || r.event_type === 'step.started').length
  const toolCalls = records.filter((r) => r.event_type === 'tool.called').length
  const artifactCount = activity.artifact_ids.length
  const running = !activity.is_terminal
  const spinning = presentation.tone === 'running'

  return (
    <Collapsible
      open={open ?? (defaultOpen ?? false)}
      onOpenChange={(o) => {
        userToggled.current = true
        setOpen(o)
      }}
      className="rounded-md border bg-muted/30"
      data-testid="agent-activity-block"
      data-task-id={taskId}
      data-tone={presentation.tone}
    >
      <CollapsibleTrigger
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/50"
        aria-label={
          (open ?? false)
            ? 'Collapse Agent execution details'
            : 'Expand Agent execution details'
        }
      >
        <Icon
          className={`h-3.5 w-3.5 shrink-0 ${TONE_CLASS[presentation.tone] ?? ''}${spinning ? ' animate-spin' : ''}`}
          aria-hidden
        />
        <span
          className="text-xs font-medium"
          data-testid="activity-block-status"
          data-tone={presentation.tone}
        >
          {presentation.label}
        </span>
        <span className="text-xs text-muted-foreground" data-testid="activity-block-summary">
          {running
            ? (activity.iteration > 0
              ? `iteration ${activity.iteration} of ${activity.max_iterations}`
              : 'starting…')
            : [
              stepCount > 0
                ? `${stepCount} step${stepCount === 1 ? '' : 's'}`
                : toolCalls > 0
                  ? `${toolCalls} capability call${toolCalls === 1 ? '' : 's'}`
                  : null,
              artifactCount > 0
                ? `${artifactCount} artifact${artifactCount === 1 ? '' : 's'}`
                : null,
            ].filter(Boolean).join(' · ')}
        </span>
        <ChevronDown
          className={`ml-auto h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${
            (open ?? false) ? 'rotate-180' : ''
          }`}
          aria-hidden
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="space-y-2 border-t px-3 py-2">
          <p className="text-xs leading-relaxed text-muted-foreground" data-testid="activity-block-hint">
            {presentation.hint}
          </p>
          {activity.plan_steps.length > 0 && (
            <div data-testid="activity-block-plan">
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                {activity.plan_revisions > 1
                  ? `Plan (re-planned, revision ${activity.plan_revisions})`
                  : 'Plan'}
              </p>
              <ul className="space-y-0.5">
                {activity.plan_steps.map((s) => (
                  <li key={s.step_id} className="flex items-center gap-1.5 text-xs">
                    <span
                      aria-hidden
                      className={
                        s.status === 'success'
                          ? 'text-emerald-600'
                          : s.status === 'running'
                            ? 'text-blue-600'
                            : s.status === 'failed'
                              ? 'text-red-600'
                              : s.status === 'superseded'
                                ? 'text-muted-foreground/60 line-through'
                                : 'text-muted-foreground'
                      }
                    >
                      {s.status === 'success' ? '✓' : s.status === 'running'
                        ? '●' : s.status === 'failed' ? '✗'
                          : s.status === 'superseded' ? '↷' : '○'}
                    </span>
                    <span className="text-muted-foreground">
                      {s.type.replace(/_/g, ' ')}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {records.length > 0 && (
            <div>
              <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                Execution timeline
              </p>
              {omitted > 0 && (
                <p
                  className="mb-1 text-xs text-muted-foreground"
                  data-testid="activity-block-omitted"
                >
                  Showing the latest {records.length} of {recordsAll.length}{' '}
                  events — the full trace is in the Activity tab.
                </p>
              )}
              <ScrollArea className="max-h-[280px] pr-1 [&>[data-radix-scroll-area-viewport]]:max-h-[280px]">
                <ul className="space-y-1.5" data-testid="activity-block-timeline">
                  {records.map((r) => (
                    <TimelineRow key={r.seq} record={r} />
                  ))}
                </ul>
              </ScrollArea>
            </div>
          )}
          {activity.artifact_ids.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5" data-testid="activity-block-artifacts">
              <FileText className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
              {activity.artifact_ids.map((id) => (
                <Badge key={id} variant="outline" className="font-mono text-[10px]">
                  {id}
                </Badge>
              ))}
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
