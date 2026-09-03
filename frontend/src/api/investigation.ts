/**
 * Typed API client for the V2 Investigation HTTP API (/api/v2).
 *
 * Single centralized base configuration (no hardcoded hosts in components).
 * The frontend talks ONLY to the Agent Client API — never to the Risk
 * Platform. All error responses are normalized into InvestigationApiError
 * with bounded server-provided messages (no stack traces surface here).
 */

import type {
  ApiErrorEnvelope,
  ArtifactsResponse,
  CreateInvestigationRequest,
  FocusAction,
  FollowUpsResponse,
  InvestigationListResponse,
  InvestigationResponse,
  TaskDetailResponse,
  TaskResultsResponse,
  TurnRequest,
  TurnResponse,
} from './types'

const API_BASE = '/api/v2'

export class InvestigationApiError extends Error {
  constructor(
    message: string,
    public statusCode: number,
    public code: string | null,
    public endpoint: string,
  ) {
    super(message)
    this.name = 'InvestigationApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch (err) {
    throw new InvestigationApiError(
      'Could not reach the investigation service.',
      0,
      'NETWORK_ERROR',
      path,
    )
  }

  if (!response.ok) {
    let code: string | null = null
    let message = `Request failed with status ${response.status}`
    try {
      const body = (await response.json()) as
        | ApiErrorEnvelope
        | { detail: string }
      if (typeof body.detail === 'object' && body.detail !== null) {
        code = body.detail.code ?? null
        message = body.detail.message ?? message
      } else if (typeof body.detail === 'string') {
        message = body.detail
      }
    } catch {
      // non-JSON error body — keep the status-based message
    }
    throw new InvestigationApiError(message, response.status, code, path)
  }
  return (await response.json()) as T
}

export const investigationApi = {
  createInvestigation(body: CreateInvestigationRequest) {
    return request<InvestigationResponse>('/investigations', {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },

  runTurn(investigationId: string, body: TurnRequest) {
    return request<TurnResponse>(
      `/investigations/${encodeURIComponent(investigationId)}/turn`,
      { method: 'POST', body: JSON.stringify(body) },
    )
  },

  getInvestigation(investigationId: string) {
    return request<InvestigationResponse>(
      `/investigations/${encodeURIComponent(investigationId)}`,
    )
  },

  getTask(taskId: string) {
    return request<TaskDetailResponse>(`/tasks/${encodeURIComponent(taskId)}`)
  },

  getTaskArtifacts(taskId: string) {
    return request<ArtifactsResponse>(
      `/tasks/${encodeURIComponent(taskId)}/artifacts`,
    )
  },

  getTaskResults(taskId: string) {
    return request<TaskResultsResponse>(
      `/tasks/${encodeURIComponent(taskId)}/results`,
    )
  },

  listInvestigations() {
    return request<InvestigationListResponse>('/investigations')
  },

  /** Preview the backend's Suggested Follow-ups for the current context,
   * optionally as it would stand after the given selection's context_action
   * (the same action the next turn will carry). Read-only; no turn is run. */
  listFollowups(investigationId: string, contextAction?: FocusAction) {
    const query = contextAction
      ? `?context_action=${encodeURIComponent(JSON.stringify(contextAction))}`
      : ''
    return request<FollowUpsResponse>(
      `/investigations/${encodeURIComponent(investigationId)}/followups${query}`,
    )
  },

  deleteInvestigation(investigationId: string) {
    return request<{ deleted: boolean }>(
      `/investigations/${encodeURIComponent(investigationId)}`,
      { method: 'DELETE' },
    )
  },
}
