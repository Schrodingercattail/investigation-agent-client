/**
 * AgentActivityPanel — the Agent Activity / Execution Trace view
 * (docs/baselines/2026-09-19-agent-activity-v1.md).
 *
 * Renders the bounded, UI-safe activity projection returned by
 * GET /tasks/{task_id}/activity. Product language: Agent Activity,
 * Execution Trace, Decision Summary, Result Summary. This view NEVER
 * shows chain-of-thought, raw model completions, raw transaction
 * datasets, or internal tool payloads — those never exist in the
 * projection by backend contract.
 *
 * Structure (baseline §5):
 *   Run header (goal · focus · status · iteration)
 *   Current plan (bounded snapshot: ✓ completed · ● current · ○ pending
 *                 · ↷ superseded)
 *   Activity timeline (sequence-ordered, human-readable titles)
 */

import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { ScrollArea } from '@/components/ui/scroll-area'
import { CheckCircle2, ChevronDown, CircleDashed, Loader2, OctagonX, ShieldAlert, Wrench } from 'lucide-react'
import type { ComponentType } from 'react'
import type { AgentActivityRecord, AgentPlanStep, TaskActivityResponse } from '@/api/types'
import { normalizeActivity } from '@/hooks/useAgentActivity'

// --- status presentation (§9: icon + text + color, never color alone) -----

const STATUS_META: Record<string, { label: string; icon: ComponentType<{ className?: string }>; className: string; spinning?: boolean }> = {
  running: { label: 'Investigating', icon: Loader2, className: 'text-blue-700 bg-blue-50 border-blue-200', spinning: true },
  re_planning: { label: 'Re-planning investigation', icon: RefreshIcon, className: 'text-amber-700 bg-amber-50 border-amber-200', spinning: true },
  retrying: { label: 'Retrying after a temporary error', icon: Loader2, className: 'text-amber-700 bg-amber-50 border-amber-200', spinning: true },
  completed: { label: 'Investigation completed', icon: CheckCircle2, className: 'text-emerald-700 bg-emerald-50 border-emerald-200' },
  blocked: { label: 'Investigation blocked', icon: ShieldAlert, className: 'text-orange-700 bg-orange-50 border-orange-200' },
  failed: { label: 'Investigation failed', icon: OctagonX, className: 'text-red-700 bg-red-50 border-red-200' },
  stopped_by_limit: { label: 'Stopped because the execution limit was reached', icon: CircleDashed, className: 'text-violet-700 bg-violet-50 border-violet-200' },
  interrupted_resumable: { label: 'Interrupted — state preserved', icon: CircleDashed, className: 'text-slate-700 bg-slate-50 border-slate-200' },
}

function RefreshIcon(props: React.SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M21 12a9 9 0 1 1-2.64-6.36" />
      <path d="M21 3v6h-6" />
    </svg>
  )
}

export function agentStatusMeta(status: string) {
  return (
    STATUS_META[status] ?? {
      label: status.replace(/_/g, ' '),
      icon: CircleDashed,
      className: 'text-slate-700 bg-slate-50 border-slate-200',
    }
  )
}

// --- run header -------------------------------------------------------------

export function AgentRunHeader({ activity }: { activity: TaskActivityResponse }) {
  const meta = agentStatusMeta(activity.agent_status)
  const Icon = meta.icon
  const focus = activity.focused_finding_id
    ? `finding ${activity.focused_finding_id}`
    : 'case level'
  return (
    <div className="space-y-2">
      <p className="text-sm font-semibold" data-testid="agent-goal">{activity.goal}</p>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="font-mono">{activity.case_id}</span>
        <span aria-hidden>·</span>
        <span>{focus}</span>
        <span aria-hidden>·</span>
        <span data-testid="agent-iteration">
          {activity.iteration === 0
            ? 'Starting…'
            : `Iteration ${activity.iteration} of ${activity.max_iterations}`}
        </span>
      </div>
      <div
        data-testid="agent-status"
        data-agent-status={activity.agent_status}
        className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium ${meta.className}`}
      >
        <Icon
          className={`h-3.5 w-3.5${meta.spinning ? ' animate-spin' : ''}`}
          aria-hidden
        />
        <span>{meta.label}</span>
        {activity.stop_reason && (
          <span className="font-normal text-muted-foreground">
            ({activity.stop_reason.replace(/_/g, ' ')})
          </span>
        )}
      </div>
    </div>
  )
}

// --- plan snapshot (§8: bounded current plan, superseded after re-plan) -----

const STEP_STATE: Record<string, { marker: string; label: string; className: string }> = {
  success: { marker: '✓', label: 'completed', className: 'text-emerald-600' },
  running: { marker: '●', label: 'current', className: 'text-blue-600' },
  pending: { marker: '○', label: 'pending', className: 'text-muted-foreground' },
  failed: { marker: '✗', label: 'failed', className: 'text-red-600' },
  superseded: { marker: '↷', label: 'superseded', className: 'text-muted-foreground/60 line-through' },
}

export function stepState(status: string) {
  return STEP_STATE[status] ?? STEP_STATE.pending
}

export function AgentPlan({ activity }: { activity: TaskActivityResponse }) {
  if (activity.plan_steps.length === 0) return null
  const planLabel =
    activity.plan_revisions > 1 ? `Plan ${activity.plan_revisions}` : 'Current plan'
  return (
    <div data-testid="agent-plan">
      <p className="mb-1 text-xs font-semibold text-muted-foreground">
        {planLabel}
        {activity.plan_revisions > 1 && ' (re-planned)'}
      </p>
      <ul className="space-y-1">
        {activity.plan_steps.map((s: AgentPlanStep) => {
          const st = stepState(s.status)
          return (
            <li key={s.step_id} className="flex items-center gap-2 text-xs">
              <span aria-hidden className={st.className}>{st.marker}</span>
              <span className={st.className === 'text-muted-foreground/60 line-through' ? st.className : ''}>
                {s.type.replace(/_/g, ' ')}
              </span>
              <span className="sr-only">({st.label})</span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

// --- timeline (§3 event → human-readable rows) -------------------------------

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

export function eventTitle(eventType: string): string {
  return EVENT_TITLES[eventType] ?? eventType.replace(/[._]/g, ' ')
}

export function AgentDecisionChip({ decision }: { decision: string }) {
  const label = decision === 'RE_PLAN' ? 'RE-PLAN' : decision
  return (
    <Badge variant="outline" data-testid="agent-decision" data-decision={decision}>
      {label}
    </Badge>
  )
}

export function AgentEvidenceRefs({ record }: { record: AgentActivityRecord }) {
  if (record.artifact_ids.length === 0) return null
  return (
    <p className="text-xs text-muted-foreground">
      Artifacts:{' '}
      {record.artifact_ids.map((id) => (
        <span key={id} className="mr-1 font-mono" data-testid="agent-artifact-ref">
          {id}
        </span>
      ))}
    </p>
  )
}

export function AgentActivityItem({ record }: { record: AgentActivityRecord }) {
  const title = eventTitle(record.event_type)
  const toolLabel = record.tool_name
    ? record.tool_name.replace(/_/g, ' ')
    : null
  const detailBits = [
    record.iteration != null ? `iteration ${record.iteration}` : null,
    record.step_type ? record.step_type.replace(/_/g, ' ') : null,
    record.outcome ? `outcome: ${record.outcome}` : null,
    record.stop_reason ? record.stop_reason.replace(/_/g, ' ') : null,
  ].filter(Boolean)
  return (
    <li className="space-y-0.5 border-l-2 border-border pl-3" data-testid="agent-activity-item" data-seq={record.seq}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium">{title}</span>
        {record.decision && <AgentDecisionChip decision={record.decision} />}
        {record.tool_name && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Wrench className="h-3 w-3" aria-hidden />
            <span className="max-w-[200px] truncate" title={record.tool_name}>
              {toolLabel}
            </span>
          </span>
        )}
        {record.outcome && (
          <Badge variant="secondary" className="text-[10px]">{record.outcome}</Badge>
        )}
      </div>
      {record.summary && (
        <p className="text-xs text-muted-foreground">{record.summary}</p>
      )}
      {detailBits.length > 0 && (
        <p className="text-[10px] text-muted-foreground/70">{detailBits.join(' · ')}</p>
      )}
      <AgentEvidenceRefs record={record} />
    </li>
  )
}

// --- panel -------------------------------------------------------------------

export function AgentActivityPanel({
  activity,
}: {
  activity: TaskActivityResponse
}) {
  const records = normalizeActivity(activity.activity)
  const planSection = <AgentPlan activity={activity} />
  return (
    <div className="space-y-3" data-testid="agent-activity-panel">
      <AgentRunHeader activity={activity} />
      {activity.plan_steps.length > 0 && (
        <Collapsible defaultOpen>
          <CollapsibleTrigger className="flex w-full items-center gap-1 text-xs font-semibold text-muted-foreground hover:text-foreground">
            <ChevronDown className="h-3.5 w-3.5" aria-hidden />
            Plan
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-2">{planSection}</CollapsibleContent>
        </Collapsible>
      )}
      <div>
        <p className="mb-1 text-xs font-semibold text-muted-foreground">Activity</p>
        <ScrollArea className="max-h-[320px] pr-2">
          <ul className="space-y-2" data-testid="agent-activity-timeline">
            {records.map((r) => (
              <AgentActivityItem key={r.seq} record={r} />
            ))}
          </ul>
        </ScrollArea>
        {activity.agent_status === 'running' && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden /> Working…
          </p>
        )}
      </div>
    </div>
  )
}
