/**
 * TypeScript contracts for the V2 Investigation HTTP API (/api/v2).
 *
 * These correspond to docs/architecture/API_CONTRACT_V1.md. They are
 * frontend contracts only — backend implementation classes are never copied.
 */

// --- Domain-shaped payloads --------------------------------------------------

export interface Investigation {
  investigation_id: string
  case_id: string
  status: 'active' | 'completed' | 'failed'
  created_at: string | null
  updated_at: string | null
}

export interface ContextPreferences {
  citation_required: boolean
  response_length: 'brief' | 'standard' | 'detailed'
  output_format: 'md'
}

export interface InvestigationContext {
  case_id: string
  focused_finding_id: string | null
  focused_event_id: string | null
  focus_source: 'user_selected' | 'agent_resolved' | 'system_default' | null
  time_window: string | null
  selected_policy_ids: string[]
  preferences: ContextPreferences
}

export interface Finding {
  finding_id: string
  case_id: string
  type: string
  title: string
  severity: 'low' | 'medium' | 'high' | 'critical' | 'unknown'
  summary: string
  evidence_refs: { kind: string; id: string }[]
  signal_refs: { signal_type: string; name: string }[]
  policy_refs: {
    citation_id: number | null
    chunk_id: string | null
    doc: string | null
    section: string | null
  }[]
  capabilities: string[]
}

export interface PlanStep {
  step_id: string
  type: string
  reason: string | null
  status: 'pending' | 'running' | 'success' | 'failed' | 'skipped' | 'rejected'
  depends_on: string[]
  tool_name: string | null
  arguments: Record<string, unknown>
  started_at: string | null
  completed_at: string | null
  error: string | null
}

export interface PlanSummary {
  plan_id: string
  goal: string
  status: string
  selected_skill: string | null
  steps: PlanStep[]
}

export interface ToolCallSummary {
  tool_call_id: string
  tool_name: string
  status: 'running' | 'success' | 'failed'
  outcome:
    | 'success'
    | 'unsupported'
    | 'empty'
    | 'integration_error'
    | 'validation_error'
    | null
  started_at: string | null
  completed_at: string | null
  error: string | null
  summary: string | null
  evidence_ref_count: number
  citation_ref_count: number
}

export interface ExecutionSummary {
  status: string
  tool_calls: ToolCallSummary[]
  errors: { code: string; message: string; step_id: string | null }[]
}

export interface Artifact {
  artifact_id: string
  investigation_id: string
  task_id: string
  scope: string
  artifact_type: string
  format: 'md'
  title: string
  content: string | null
  storage_ref: string | null
  source_tool_calls: string[]
  created_at: string | null
}

export interface FollowUp {
  follow_up_id: string
  label: string
  intent: string
  required_capabilities: string[]
  applicable_context: 'finding' | 'timeline_event' | 'case'
  target_skill: string | null
  target_step: string | null
  reason: string | null
  /** agent_intent: click submits the intent as the next Agent turn.
   * ui_navigation: click performs a pure UI action (open a panel) —
   * nothing is sent to the Agent. */
  action_kind?: 'agent_intent' | 'ui_navigation'
}

export interface Task {
  task_id: string
  investigation_id: string
  type: string
  status: 'pending' | 'planning' | 'executing' | 'completed' | 'failed' | 'cancelled'
  user_request: string
  plan_id: string | null
  selected_skill: string | null
  tool_call_ids: string[]
  artifact_ids: string[]
  started_at: string | null
  completed_at: string | null
  error: string | null
}

// --- Envelopes ---------------------------------------------------------------

export interface TurnResponse {
  investigation_id: string
  status: 'completed' | 'failed' | 'unsupported' | 'execution_failed'
  response: string
  context: InvestigationContext
  task: Task
  plan: PlanSummary | null
  execution: ExecutionSummary | null
  artifacts: Artifact[]
  follow_ups: FollowUp[]
  context_changed: boolean
}

export interface InvestigationResponse {
  investigation: Investigation
  context: InvestigationContext
  tasks: Task[]
  /** Canonical Case Intake turn request — present on creation only. The
   * client submits this as the first conversational turn. */
  intake_message?: string | null
}

export interface InvestigationHistoryItem {
  investigation_id: string
  case_id: string
  status: Investigation['status']
  created_at: string | null
  updated_at: string | null
}

export interface InvestigationListResponse {
  investigations: InvestigationHistoryItem[]
}

export interface TaskDetailResponse {
  task: Task
  plan: PlanSummary | null
  tool_calls: ToolCallSummary[]
  artifacts: Artifact[]
  selected_skill: string | null
  error: string | null
  /** Composed human-readable turn response (conversation restore). */
  task_response_text?: string | null
  /** This Agent message's own Suggested Follow-ups (restore). */
  task_follow_ups?: FollowUp[]
}

export interface ArtifactsResponse {
  task_id: string
  artifacts: Artifact[]
}

/** Read-only view of one task's normalized tool results (Agent-domain
 * shapes only — findings, timeline events, explanations, policy context). */
export interface TaskResultsResponse {
  task_id: string
  results: {
    tool_call_id: string
    tool_name: string
    findings?: Finding[]
    timeline_events?: TimelineEventData[]
    signal_explanation?: {
      signal_type: string
      rule?: Record<string, unknown>
      evidence_missing: boolean
      next_data_needed: string[]
    }
    policy_context?: {
      topic: string
      matches: {
        citation_id: number | null
        chunk_id: string | null
        document: string
        section: string
        snippet: string
        relevance: number
      }[]
      evidence_missing: boolean
    }
    artifact?: Record<string, unknown>
    evidence_missing?: boolean
    next_data_needed?: string[]
  }[]
}

export interface TimelineEventData {
  event_id: string
  finding_id: string
  timestamp: string
  event_type: string
  summary: string
  evidence_refs: { kind: string; id: string }[]
  signal_refs: { signal_type: string; name: string }[]
  policy_refs: unknown[]
  importance: string | null
}

/** Read-only preview of the backend's Suggested Follow-ups for the current
 * persisted context (GET /investigations/{id}/followups). Same server-owned
 * selection pipeline as turn responses — the frontend never generates or
 * filters follow-ups. */
export interface FollowUpsResponse {
  investigation_id: string
  context: InvestigationContext
  follow_ups: FollowUp[]
}

// --- Requests ------------------------------------------------------------------

export interface CreateInvestigationRequest {
  /** Canonical case ID (e.g. "U00299")… */
  case_id?: string
  /** …or raw user input ("00299", "investigate case 00299") which the
   * backend resolves server-side via Case Reference Resolution. */
  message?: string
  case_reference?: string
  preferences?: Partial<ContextPreferences>
}

export interface FocusAction {
  type: 'focus_finding' | 'focus_event' | 'clear_focus'
  finding_id?: string
  event_id?: string
}

export interface TurnRequest {
  message?: string
  context_action?: FocusAction
  follow_up_id?: string
}

// --- Errors ----------------------------------------------------------------------

export interface ApiErrorEnvelope {
  detail: {
    code: string
    message: string
  }
}
