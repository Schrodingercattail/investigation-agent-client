// Task types matching the backend models

export const ProvenanceType = {
  DIRECT_EVIDENCE: 'direct_evidence',
  RULE_DERIVED: 'rule_derived',
  ML_DERIVED: 'ml_derived',
  GRAPH_DERIVED: 'graph_derived',
  PRIMARY_REASON: 'primary_reason',
  UNKNOWN: 'unknown',
} as const

export type ProvenanceType = typeof ProvenanceType[keyof typeof ProvenanceType]

export interface FindingProvenance {
  source: string
  type: ProvenanceType
  source_finding_id: string
  evidence_ids: string[]
  policy_ids: string[]
}

export interface Finding {
  finding_id: string
  claim: string
  provenance: FindingProvenance
}

export const TaskStatus = {
  DRAFT: 'draft',
  READY: 'ready',
  RUNNING: 'running',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
} as const

export type TaskStatus = typeof TaskStatus[keyof typeof TaskStatus]

export const ExecutionMode = {
  AGENT: 'agent',
  DETERMINISTIC: 'deterministic',
} as const

export type ExecutionMode = typeof ExecutionMode[keyof typeof ExecutionMode]

export const StepStatus = {
  PENDING: 'pending',
  RUNNING: 'running',
  SUCCESS: 'success',
  FAILED: 'failed',
  SKIPPED: 'skipped',
} as const

export type StepStatus = typeof StepStatus[keyof typeof StepStatus]

export const ToolCallStatus = {
  RUNNING: 'running',
  SUCCESS: 'success',
  FAILED: 'failed',
} as const

export type ToolCallStatus = typeof ToolCallStatus[keyof typeof ToolCallStatus]

export const ArtifactType = {
  FINDINGS: 'findings',
  ACTIONS: 'actions',
  CITATIONS: 'citations',
  NARRATIVE: 'narrative',
} as const

export type ArtifactType = typeof ArtifactType[keyof typeof ArtifactType]

export interface ToolCall {
  id: string
  tool_name: string
  args: Record<string, unknown>
  status: ToolCallStatus
  started_at: string | null
  completed_at: string | null
  latency_ms: number | null
  output_summary: string | null
  output: unknown | null
  error: string | null
}

export interface Step {
  id: string
  name: string
  description: string
  tool_name: string
  status: StepStatus
  tool_call: ToolCall | null
  started_at: string | null
  completed_at: string | null
  error: string | null
}

export interface Artifact {
  id: string
  type: ArtifactType
  title: string
  data: unknown
  created_at: string | null
}

export interface Task {
  id: string
  user_intent: string
  case_id: string
  execution_mode: ExecutionMode
  status: TaskStatus
  steps: Step[]
  artifacts: Artifact[]
  acceptance_status: string
  created_at: string | null
  updated_at: string | null
}

export interface CreateTaskRequest {
  user_intent: string
  execution_mode?: ExecutionMode
}

// Risk Summary types
export interface DetectionSignals {
  ml_score?: number
  rule_score?: number
  graph_score?: number
}

export interface RiskSummary {
  case_id: string
  risk_score: number
  risk_level: string
  primary_reason?: string
  recommended_action?: string
  detection_signals?: DetectionSignals
}
