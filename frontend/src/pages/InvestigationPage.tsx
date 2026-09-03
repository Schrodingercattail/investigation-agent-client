/**
 * V2 Agent Investigation workspace — the primary Week 1 experience.
 *
 * The frontend is a CLIENT of the Agent runtime: it renders the structured
 * state returned by /api/v2 and never duplicates backend intelligence
 * (skills, capabilities, planning, tool selection, follow-up eligibility,
 * provenance are all backend-owned).
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ArtifactsPanel } from '@/components/investigation/ArtifactsPanel'
import { InvestigationHistory } from '@/components/investigation/InvestigationHistory'
import { ConversationPanel, type ConversationTurn } from '@/components/investigation/ConversationPanel'
import { ContextPanel } from '@/components/investigation/ContextPanel'
import { FindingsPanel } from '@/components/investigation/FindingsPanel'
import { TimelineView } from '@/components/investigation/TimelineView'
import type {
  Artifact,
  Finding,
  FocusAction,
  FollowUp,
  InvestigationContext,
  InvestigationHistoryItem,
  TaskResultsResponse,
  TurnResponse,
} from '@/api/types'
import { InvestigationApiError, investigationApi } from '@/api/investigation'
import { Bot, Loader2, PanelLeftOpen, PanelRightOpen, SendHorizonal } from 'lucide-react'

type TurnPhase = 'idle' | 'creating' | 'loading' | 'error'

interface WorkspaceState {
  investigationId: string | null
  context: InvestigationContext | null
  findings: Finding[]
  turns: ConversationTurn[]
  artifacts: Artifact[]
  phase: TurnPhase
  errorMessage: string | null
}

const initialState: WorkspaceState = {
  investigationId: null,
  context: null,
  findings: [],
  turns: [],
  artifacts: [],
  phase: 'idle',
  errorMessage: null,
}

/** UI navigation state only — NEVER a copy of investigation state. The
 * backend is the source of truth; the workspace reloads from /api/v2. */
const CURRENT_INV_KEY = 'investigation.currentId'

function readPersistedInvestigationId(): string | null {
  try {
    return localStorage.getItem(CURRENT_INV_KEY)
  } catch {
    return null
  }
}

function persistInvestigationId(id: string | null): void {
  try {
    if (id) localStorage.setItem(CURRENT_INV_KEY, id)
    else localStorage.removeItem(CURRENT_INV_KEY)
  } catch {
    // storage unavailable — navigation state only, safe to ignore
  }
}

export function InvestigationPage() {
  const [state, setState] = useState<WorkspaceState>(initialState)
  const [history, setHistory] = useState<InvestigationHistoryItem[]>([])
  const [input, setInput] = useState('')
  const [pendingFocus, setPendingFocus] = useState<
    | { type: 'focus_finding'; finding_id: string }
    | { type: 'focus_event'; finding_id: string; event_id: string }
    | { type: 'clear_focus' }
    | null
  >(null)
  const [leftSheet, setLeftSheet] = useState(false)
  const [rightSheet, setRightSheet] = useState(false)
  /** Backend-provided follow-ups for the CURRENT context (previewed via the
   * read-only followups endpoint once a finding is selected). Never
   * generated client-side; superseded by each turn response's own set. */
  const [previewFollowUps, setPreviewFollowUps] = useState<FollowUp[]>([])
  /** Controlled value for the right-side Context/Artifacts tabs so UI
   * navigation follow-ups can focus the Artifacts panel. */
  const [rightTab, setRightTab] = useState<'context' | 'artifacts'>('context')

  const setPhase = (phase: TurnPhase, errorMessage: string | null = null) =>
    setState((s) => ({ ...s, phase, errorMessage }))

  function absorbTurn(response: TurnResponse) {
    // Findings and timeline rows come from the task results API — the
    // bounded execution summary intentionally does not carry raw payloads.
    persistInvestigationId(response.investigation_id)
    void refreshHistory()
    // the turn's own backend follow-ups supersede any previewed set
    setPreviewFollowUps(response.follow_ups)
    setState((s) => ({
      ...s,
      phase: 'idle',
      investigationId: response.investigation_id,
      context: response.context,
      artifacts: response.artifacts.length
        ? [...s.artifacts, ...response.artifacts]
        : s.artifacts,
      turns: [
        ...s.turns,
        {
          id: `a-${response.task.task_id}`,
          role: 'agent',
          text: response.response,
          task: response.task,
          plan: response.plan,
          execution: response.execution,
          artifacts: response.artifacts,
          followUps: response.follow_ups,
          focusedEventId: response.context.focused_event_id,
        },
      ],
    }))
  }

  const errorMessage = (err: unknown): string =>
    err instanceof InvestigationApiError
      ? err.message // bounded server message; never a stack trace
      : 'Unexpected client error.'

  /** Reload the investigation history list from the backend. */
  const refreshHistory = useCallback(async () => {
    try {
      const listing = await investigationApi.listInvestigations()
      setHistory(listing.investigations)
    } catch {
      // history is best-effort; never blocks the workspace
    }
  }, [])

  /** Reopen an investigation from server state: context + task history +
   * per-task results reconstruct the conversation. Nothing is re-executed. */
  const openInvestigation = useCallback(
    async (investigationId: string) => {
      setPhase('loading')
      try {
        const stateData = await investigationApi.getInvestigation(investigationId)
        const restoredContext = stateData.context
        const restoredTurns: ConversationTurn[] = []
        const restoredArtifacts: Artifact[] = []
        let findings: Finding[] = []
        let timelineEvents: import('@/api/types').TimelineEventData[] = []

        for (const task of stateData.tasks) {
          try {
            const detail = await investigationApi.getTask(task.task_id)
            // EVERY recorded task is a conversation turn — including guided
            // rejections (cross-case requests, unresolved references,
            // planning failures) that never produced a plan. Their persisted
            // composed response is part of the investigation record and
            // must survive a refresh (P12/P14).
            if (detail.plan || detail.task_response_text) {
              restoredTurns.push({
                id: `a-${task.task_id}`,
                role: 'agent',
                // the composed response the Agent actually gave, when
                // persisted; otherwise the request text as a fallback
                text: detail.task_response_text ?? task.user_request,
                task,
                plan: detail.plan,
                execution: {
                  status: detail.task.status,
                  tool_calls: detail.tool_calls,
                  errors: [],
                },
                artifacts: detail.artifacts,
                // this message's own backend follow-ups (chips belong to
                // their message and must survive a refresh)
                followUps: detail.task_follow_ups ?? [],
              })
            }
            restoredArtifacts.push(...detail.artifacts)
            // restore structured result data for the workspace panels
            const results = await investigationApi.getTaskResults(task.task_id)
            for (const r of results.results) {
              if (r.findings?.length) findings = r.findings
              if (r.timeline_events?.length) timelineEvents = r.timeline_events
            }
          } catch {
            // per-task detail is best-effort during restore
          }
        }

        // the user-side entries of the conversation are reconstructed from
        // the persisted task user_requests (server-owned history)
        const conversation: ConversationTurn[] = []
        for (const t of restoredTurns) {
          conversation.push({
            id: `u-${t.id}`,
            role: 'user',
            text: t.task?.user_request ?? '',
          })
          conversation.push(t)
        }

        setState({
          investigationId: investigationId,
          context: restoredContext,
          findings,
          turns: conversation,
          artifacts: restoredArtifacts,
          phase: 'idle',
          errorMessage: null,
        })
        void timelineEvents
        setPendingFocus(null)
        setPreviewFollowUps([])
        persistInvestigationId(investigationId)
        // preview follow-ups for the restored (possibly focused) context
        void refreshPreviewFollowUps(investigationId)
      } catch (err) {
        setPhase('error', errorMessage(err))
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  // On mount: load history; restore the last-open investigation so refresh
  // never creates a new investigation.
  useEffect(() => {
    void refreshHistory()
    const saved = readPersistedInvestigationId()
    if (saved) {
      void openInvestigation(saved)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** Explicit New Investigation: fresh workspace; the previous investigation
   * is untouched and remains in history. */
  const newInvestigation = useCallback(() => {
    setState(initialState)
    setPendingFocus(null)
    setPreviewFollowUps([])
    setInput('')
    persistInvestigationId(null)
    void refreshHistory()
  }, [refreshHistory])

  /** Delete: explicit, server-side; clears the workspace if it was current. */
  const deleteInvestigation = useCallback(
    async (investigationId: string) => {
      try {
        await investigationApi.deleteInvestigation(investigationId)
      } catch (err) {
        setPhase('error', errorMessage(err))
        return
      }
      if (state.investigationId === investigationId) {
        newInvestigation()
      } else {
        void refreshHistory()
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.investigationId, newInvestigation, refreshHistory],
  )

  /** Refresh findings from the task results API (read-only view of the
   * fetch_case result owned by the backend). */
  const refreshPreviewFollowUps = useCallback(
    async (investigationId: string, contextAction?: FocusAction) => {
      try {
        const payload = await investigationApi.listFollowups(
          investigationId, contextAction,
        )
        setPreviewFollowUps(payload.follow_ups)
      } catch {
        // preview is best-effort; free-form chat is always available
        setPreviewFollowUps([])
      }
    },
    [],
  )

  const refreshFindings = useCallback(
    async (taskId: string) => {
      try {
        const payload: TaskResultsResponse = await investigationApi.getTaskResults(taskId)
        const findingResult = payload.results.find((r) => r.findings)
        const findings = findingResult?.findings ?? []
        if (findings.length > 0) {
          setState((s) => ({ ...s, findings }))
        }
        const timelineResult = payload.results.find((r) => r.timeline_events)
        const events = timelineResult?.timeline_events ?? []
        if (events.length > 0) {
          setState((s) => ({
            ...s,
            turns: s.turns.map((t, i) =>
              i === s.turns.length - 1 && t.role === 'agent'
                ? { ...t, timelineEvents: events }
                : t,
            ),
          }))
        }
      } catch {
        // results view is best-effort; the turn response already rendered
      }
    },
    [],
  )

  /** Create investigation + run Turn 1 in one action. */
  const startInvestigation = useCallback(
    async (rawMessage: string) => {
      setPhase('creating')
      setState((s) => ({
        ...s,
        turns: [
          ...s.turns,
          { id: `u-${Date.now()}`, role: 'user' as const, text: rawMessage },
        ],
      }))
      try {
        const created = await investigationApi.createInvestigation({
          message: rawMessage,
        })
        // Case Intake runs ONCE, using the server-provided canonical intake
        // message — the raw case-reference text is an identification input
        // and must never be re-sent as a conversational turn.
        const intakeMessage = created.intake_message
        if (!intakeMessage) {
          setPhase('error', 'Investigation created, but no intake request was returned.')
          return
        }
        const response = await investigationApi.runTurn(
          created.investigation.investigation_id,
          { message: intakeMessage },
        )
        absorbTurn(response)
        await refreshFindings(response.task.task_id)
      } catch (err) {
        setPhase('error', errorMessage(err))
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [refreshFindings],
  )

  /** Submit a normal turn (typed message or follow-up click). */
  const submitTurn = useCallback(
    async (opts?: { message?: string; followUp?: FollowUp }) => {
      const message = opts?.message ?? input
      const followUp = opts?.followUp
      if (!state.investigationId) {
        // No investigation yet: the RAW message is sent to the API, which
        // resolves the case reference server-side (e.g. "00299" → U00299).
        await startInvestigation(message)
        setInput('')
        return
      }
      if (!opts?.followUp && !message.trim()) return

      const userText = followUp ? followUp.label : message
      setState((s) => ({
        ...s,
        turns: [...s.turns, { id: `u-${Date.now()}`, role: 'user' as const, text: userText }],
      }))
      setPhase('loading')
      setInput('')

      try {
        // A follow-up click carries the canonical intent AND any pending
        // selection (its context_action) — "this finding" in the intent
        // refers to that selection, which the server applies for the turn.
        const body = followUp
          ? {
              follow_up_id: followUp.follow_up_id,
              ...(pendingFocus ? { context_action: pendingFocus } : {}),
            }
          : {
              message,
              ...(pendingFocus ? { context_action: pendingFocus } : {}),
            }
        const response = await investigationApi.runTurn(state.investigationId, body)
        absorbTurn(response)
        if (response.execution?.tool_calls.some((tc) => tc.outcome === 'success')) {
          await refreshFindings(response.task.task_id)
        }
        if (pendingFocus) setPendingFocus(null)
      } catch (err) {
        setPhase('error', errorMessage(err))
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.investigationId, input, pendingFocus, startInvestigation, refreshFindings],
  )

  /** Finding selection: pending context action for the NEXT turn (the
   * backend applies it; the frontend never mutates server context). The
   * backend's read-only followups endpoint previews what it will offer for
   * this selection — displayed immediately, still executed via the next
   * turn. */
  const selectFinding = (findingId: string) => {
    const action = { type: 'focus_finding' as const, finding_id: findingId }
    setPendingFocus(action)
    if (state.investigationId) {
      void refreshPreviewFollowUps(state.investigationId, action)
    }
  }

  const selectEvent = (eventId: string) => {
    const pendingFinding =
      pendingFocus && pendingFocus.type !== 'clear_focus'
        ? pendingFocus.finding_id
        : null
    const findingId =
      pendingFinding ?? state.context?.focused_finding_id ?? 'F3'
    const action = {
      type: 'focus_event' as const,
      finding_id: findingId,
      event_id: eventId,
    }
    setPendingFocus(action)
    if (state.investigationId) {
      void refreshPreviewFollowUps(state.investigationId, action)
    }
  }

  const clearFocus = () => {
    setPendingFocus({ type: 'clear_focus' })
    setPreviewFollowUps([])
  }

  /** Pure UI navigation (P13): open/focus a panel. No Agent turn, no Task,
   * no Planner — this never enters the investigation pipeline. */
  const navigateTo = (f: FollowUp) => {
    // The only Week 1 ui_navigation follow-up opens the Artifacts panel.
    setRightTab('artifacts')
    void f
  }

  const latestTurn = [...state.turns].reverse().find((t) => t.role === 'agent')
  const timelineEvents = useMemo(() => latestTurn?.timelineEvents ?? [], [latestTurn])

  // Selection acknowledgment (presentation-only): when a finding/event is
  // selected, the Agent message confirms the focus and carries the
  // backend-previewed Suggested Follow-ups at its bottom. Superseded by the
  // next real turn.
  const focusedFindingTitle = state.findings.find(
    (f) =>
      f.finding_id ===
      (pendingFocus && pendingFocus.type !== 'clear_focus'
        ? pendingFocus.finding_id
        : null),
  )?.title
  const acknowledgment: ConversationTurn | null = useMemo(() => {
    if (!pendingFocus || !state.investigationId) return null
    if (pendingFocus.type === 'clear_focus') return null
    if (state.phase !== 'idle') return null
    const findingLabel = focusedFindingTitle
      ? `${pendingFocus.finding_id} — ${focusedFindingTitle.replace(/\.$/, '')}`
      : pendingFocus.finding_id
    return {
      id: 'ack-selection',
      role: 'agent',
      text:
        pendingFocus.type === 'focus_event'
          ? `You're now investigating event ${pendingFocus.event_id} of ${findingLabel}.`
          : `You're now investigating ${findingLabel}.`,
      followUps: previewFollowUps,
    }
  }, [pendingFocus, state.investigationId, state.phase, focusedFindingTitle, previewFollowUps])

  const findingsPanel = (
    <FindingsPanel
      findings={state.findings}
      focusedFindingId={
        pendingFocus && pendingFocus.type !== 'clear_focus'
          ? pendingFocus.finding_id
          : state.context?.focused_finding_id ?? null
      }
      onSelect={selectFinding}
      loading={state.phase === 'creating'}
    >
      {timelineEvents.length > 0 && (
        <Card>
          <CardContent className="p-3">
            <p className="mb-2 text-sm font-semibold">Timeline</p>
            <TimelineView
              events={timelineEvents}
              focusedEventId={
                pendingFocus && pendingFocus.type === 'focus_event'
                  ? pendingFocus.event_id
                  : state.context?.focused_event_id ?? null
              }
              onSelect={selectEvent}
            />
          </CardContent>
        </Card>
      )}
    </FindingsPanel>
  )
  const artifactsPanel = <ArtifactsPanel artifacts={state.artifacts} />
  // Effective focus for DISPLAY: the pending selection wins until the next
  // turn persists it — the panel reflects what the user just chose, while
  // the backend context stays the authoritative state for turns.
  const effectiveContext = useMemo(() => {
    if (!state.context) return null
    if (!pendingFocus) return state.context
    if (pendingFocus.type === 'clear_focus') {
      return {
        ...state.context,
        focused_finding_id: null,
        focused_event_id: null,
        focus_source: null,
      }
    }
    return {
      ...state.context,
      focused_finding_id: pendingFocus.finding_id,
      focused_event_id:
        pendingFocus.type === 'focus_event' ? pendingFocus.event_id : null,
      focus_source: 'user_selected' as const,
    }
  }, [state.context, pendingFocus])
  const contextPanel = state.context ? (
    <ContextPanel
      context={effectiveContext!}
      findings={state.findings}
      onClearFocus={clearFocus}
    />
  ) : null

  // Three complementary paths (never competing): A) select a finding → the
  // Agent response explains the focus and carries the backend's Suggested
  // Follow-ups at its bottom; B) select a finding → type free-form; C) click
  // a suggested follow-up. Chips render ONLY where they belong: at the
  // bottom of each Agent message (ConversationPanel), never as a persistent
  // global bar and never duplicated in the Context panel.
  //
  // Selection before the next turn: the pending context_action previews the
  // backend's own chips inside the selection acknowledgment message below.

  // Left column: two fixed, independently scrollable vertical areas
  // (~50/50). Neither area's content can resize or displace the other; the
  // column height is pinned by the grid row it lives in.
  const leftPanel = (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className="flex min-h-0 flex-1 basis-0 flex-col">
        <InvestigationHistory
          investigations={history}
          currentInvestigationId={state.investigationId}
          onOpen={(id) => void openInvestigation(id)}
          onDelete={(id) => void deleteInvestigation(id)}
          onNewInvestigation={newInvestigation}
          disabled={state.phase === 'loading' || state.phase === 'creating'}
        />
      </div>
      <Separator className="shrink-0" />
      <div className="flex min-h-0 flex-1 basis-0 flex-col">
        {findingsPanel}
      </div>
    </div>
  )

  const rightPanel = (
    <div className="flex h-full flex-col gap-3 overflow-y-auto">
      {contextPanel}
      {artifactsPanel}
    </div>
  )

  return (
    <div className="flex h-screen flex-col bg-background">
      <header className="flex items-center gap-3 border-b px-4 py-2.5">
        <Bot className="h-5 w-5 text-primary" aria-hidden />
        <h1 className="text-sm font-semibold">Investigation Workspace</h1>
        {state.context && (
          <>
            <Separator orientation="vertical" className="h-4" />
            <span className="font-mono text-xs text-muted-foreground">{state.context.case_id}</span>
            <Badge variant="outline">investigation</Badge>
            {state.context.focus_source && (
              <Badge variant="secondary">{state.context.focus_source.replace('_', ' ')}</Badge>
            )}
          </>
        )}
        <div className="ml-auto flex gap-1 lg:hidden">
          <Sheet open={leftSheet} onOpenChange={setLeftSheet}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="sm" aria-label="Open findings panel">
                <PanelLeftOpen className="h-4 w-4" aria-hidden />
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="w-80 overflow-y-auto p-3">
              <SheetTitle className="sr-only">Findings</SheetTitle>
              {leftPanel}
            </SheetContent>
          </Sheet>
          <Sheet open={rightSheet} onOpenChange={setRightSheet}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="sm" aria-label="Open artifacts and context panel">
                <PanelRightOpen className="h-4 w-4" aria-hidden />
              </Button>
            </SheetTrigger>
            <SheetContent side="right" className="w-96 overflow-y-auto p-3">
              <SheetTitle className="sr-only">Artifacts and context</SheetTitle>
              <div className="space-y-3">{rightPanel}</div>
            </SheetContent>
          </Sheet>
        </div>
      </header>

      <div className="grid flex-1 grid-cols-1 gap-3 overflow-hidden p-3 lg:grid-cols-[300px_minmax(0,1fr)_340px]">
        <aside className="hidden min-h-0 lg:block">{leftPanel}</aside>

        <main className="flex min-h-0 flex-col gap-3">
          <div className="min-h-0 flex-1 rounded-lg border">
            <ConversationPanel
              turns={state.turns}
              loading={state.phase === 'loading' || state.phase === 'creating'}
              error={state.phase === 'error' ? state.errorMessage : null}
              focusedEventId={state.context?.focused_event_id ?? null}
              onSelectEvent={selectEvent}
              onSelectFollowUp={(f) => void submitTurn({ followUp: f })}
              onNavigate={navigateTo}
              followUpsDisabled={state.phase !== 'idle'}
              acknowledgment={acknowledgment}
            />
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              void submitTurn()
            }}
          >
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={
                state.investigationId
                  ? 'Ask about the investigation, or select a finding…'
                  : 'Start an investigation, e.g. investigate case 00299'
              }
              aria-label="Investigation message"
            />
            <Button
              type="submit"
              disabled={state.phase === 'loading' || state.phase === 'creating'}
            >
              {state.phase === 'loading' || state.phase === 'creating' ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : (
                <SendHorizonal className="h-4 w-4" aria-hidden />
              )}
              <span className="sr-only">Send message</span>
            </Button>
          </form>
        </main>

        <aside className="hidden min-h-0 overflow-hidden lg:block">
          <Tabs
            value={rightTab}
            onValueChange={(v) => setRightTab(v as 'context' | 'artifacts')}
            className="flex h-full min-h-0 flex-col"
          >
            <TabsList className="shrink-0">
              <TabsTrigger value="context" className="flex-1">Context</TabsTrigger>
              <TabsTrigger value="artifacts" className="flex-1">
                Artifacts{state.artifacts.length > 0 ? ` (${state.artifacts.length})` : ''}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="context" className="mt-3 min-h-0 flex-1 overflow-y-auto">{contextPanel}</TabsContent>
            <TabsContent value="artifacts" className="mt-3 min-h-0 flex-1 overflow-hidden">
              {artifactsPanel}
            </TabsContent>
          </Tabs>
        </aside>
      </div>
    </div>
  )
}
