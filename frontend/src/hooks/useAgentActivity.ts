/**
 * useAgentActivity — polling hook for the Agent Activity view
 * (docs/baselines/2026-09-19-agent-activity-v1.md §4/§16).
 *
 * V1 synchronization is POLLING (no WebSocket/SSE): 2s while the run is
 * active, stopping at any terminal agent_status. Skips the fetch while
 * the page is hidden (simple visibility optimization) and deduplicates
 * records by sequence number defensively (baseline §4).
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { InvestigationApiError, investigationApi } from '@/api/investigation'
import type { AgentActivityRecord, TaskActivityResponse } from '@/api/types'

const POLL_INTERVAL_MS = 2000
/** Consecutive empty (404/error) polls tolerated before the hook accepts
 * notFound — grace for the goal-run startup race where the first poll can
 * precede the loop thread's first state persistence (AL-2.5). */
const MISSING_SNAPSHOT_RETRIES = 5

export interface AgentActivityState {
  activity: TaskActivityResponse | null
  loading: boolean
  error: string | null
  /** 404 NO_AGENT_ACTIVITY: the task is a pre-loop turn-local task —
   * the caller falls back to the existing TaskDetail rendering. */
  notFound: boolean
}

/** Deduplicate by seq and keep a stable sequence ordering (baseline §4:
 * defensive handling of duplicate/out-of-order records). */
export function normalizeActivity(
  records: AgentActivityRecord[],
): AgentActivityRecord[] {
  const bySeq = new Map<number, AgentActivityRecord>()
  for (const r of records) bySeq.set(r.seq, r)
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq)
}

export function useAgentActivity(
  taskId: string | null,
  opts?: { poll?: boolean },
): AgentActivityState & { refresh: () => Promise<TaskActivityResponse | null> } {
  const poll = opts?.poll ?? true
  const [activity, setActivity] = useState<TaskActivityResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notFound, setNotFound] = useState(false)

  // the entity this hook instance is currently scoped to; a response that
  // belongs to an older id is discarded (QA-03: no stale cross-entity updates)
  const taskRef = useRef(taskId)
  taskRef.current = taskId

  const refresh = useCallback(async (): Promise<TaskActivityResponse | null> => {
    const id = taskRef.current
    if (!id) return null
    try {
      const data = await investigationApi.getTaskActivity(id)
      if (taskRef.current !== id) return null   // entity switched mid-flight
      setActivity(data)
      setNotFound(false)
      setError(null)
      return data
    } catch (err) {
      if (taskRef.current !== id) return null   // stale error — discard
      if (err instanceof InvestigationApiError && err.statusCode === 404) {
        setNotFound(true)
        setActivity(null)
        return null
      }
      // transient polling errors never clear the last good snapshot
      setError(
        err instanceof InvestigationApiError
          ? err.message
          : 'Unexpected client error.',
      )
      return null
    }
  }, [])

  useEffect(() => {
    setActivity(null)
    setNotFound(false)
    setError(null)
    if (!taskId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | null = null
    // consecutive empty polls before settling into notFound (grace for the
    // goal-run startup race; see tick below)
    let misses = 0

    const tick = async () => {
      if (cancelled) return
      // simple visibility optimization: skip the fetch while hidden
      if (typeof document !== 'undefined' && document.hidden) {
        timer = setTimeout(tick, POLL_INTERVAL_MS)
        return
      }
      const snapshot = await refresh()
      if (cancelled) return
      if (!snapshot) {
        // A 404/error here is usually TRANSIENT for a just-started goal:
        // the loop thread persists its first agent state milliseconds
        // after the HTTP response, so an immediate first poll can beat it
        // (AL-2.5 browser E2E race: one early 404 used to permanently
        // disable the live Activity view — "No Agent Loop run yet" while
        // the run was actually executing). Keep polling for a bounded
        // grace window instead; a genuinely non-loop task settles into
        // notFound shortly after.
        misses += 1
        if (poll && misses <= MISSING_SNAPSHOT_RETRIES) {
          timer = setTimeout(tick, POLL_INTERVAL_MS)
        }
        return
      }
      misses = 0
      if (poll && !snapshot.is_terminal) {
        timer = setTimeout(tick, POLL_INTERVAL_MS)
      }
    }

    setLoading(true)
    void tick().finally(() => setLoading(false))
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [taskId, poll, refresh])

  return { activity, loading, error, notFound, refresh }
}
