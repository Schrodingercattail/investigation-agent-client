import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import type {
  Artifact,
  ExecutionSummary,
  FollowUp,
  PlanSummary,
  Task,
} from '@/api/types'
import { Bot, CircleAlert, User } from 'lucide-react'
import { Fragment, useEffect, useRef } from 'react'
import { PlanCard } from './PlanCard'
import { FollowUpChips } from './FollowUpChips'
import { TimelineView, type TimelineEventData } from './TimelineView'
import { TaskStatus } from './TaskStatus'

export interface ConversationTurn {
  id: string
  role: 'user' | 'agent'
  text: string
  /** structured attachments (agent turns only) */
  task?: Task | null
  plan?: PlanSummary | null
  execution?: ExecutionSummary | null
  artifacts?: Artifact[]
  followUps?: FollowUp[]
  timelineEvents?: TimelineEventData[]
  focusedEventId?: string | null
}

interface ConversationPanelProps {
  turns: ConversationTurn[]
  loading: boolean
  error: string | null
  onSelectEvent: (eventId: string) => void
  focusedEventId: string | null
  onSelectFollowUp: (f: FollowUp) => void
  /** Handler for ui_navigation follow-ups (pure UI action, no Agent turn).
   * Receives the follow-up so the page can decide the navigation target. */
  onNavigate: (f: FollowUp) => void
  followUpsDisabled: boolean
  /** Authoritative current finding focus (InvestigationContext). Drives
   * whether intake guidance ("Select a finding…") is applicable: shown
   * only when NO finding is focused (P12 — guidance matches state). */
  focusedFindingId: string | null
}

/**
 * Chat-style conversation workspace. Structured attachments (plan,
 * execution, artifacts, follow-ups) are visually distinct from natural
 * language. Everything rendered comes from the API response.
 */

/** Guidance applicability is a PRESENTATION concern driven by CURRENT focus
 * state (P12): the intake response legitimately contains the guidance
 * sentence "Select a finding from the Findings panel on the left to
 * continue the investigation." — written when nothing was focused. Once a
 * finding IS focused that sentence is no longer applicable, so it is
 * filtered from display (the same way unresolvable citation markers are
 * filtered). The regex must match the COMPLETE sentence including its
 * continuation clause — a partial match leaves a grammatical fragment
 * ("… to continue the investigation."). The stored turn text is untouched;
 * only the rendered view is scoped to the authoritative focus state. */
const SELECT_FINDING_SENTENCE_RE =
  /\n?Select a finding from the Findings panel on the left to continue the investigation\.?(\n)?/g

export function renderTurnText(
  text: string,
  focusedFindingId: string | null,
): string {
  if (focusedFindingId) {
    return text
      .replace(SELECT_FINDING_SENTENCE_RE, '\n')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trimEnd()
  }
  return text
}
export function ConversationPanel({
  turns,
  loading,
  error,
  onSelectEvent,
  focusedEventId,
  onSelectFollowUp,
  onNavigate,
  followUpsDisabled,
  focusedFindingId,
}: ConversationPanelProps) {
  // Auto-scroll: whenever the rendered conversation content changes (a new
  // user turn, agent response, or focus confirmation is appended), bring the
  // newest content into view. Keyed to rendered turn content — NOT to the
  // submit event — so async assistant responses scroll too. The sentinel
  // sits after the last turn; we scroll its nearest scrollable ancestor to
  // the sentinel's bottom (the Radix viewport is a nested scroll container,
  // so plain scrollIntoView can target the wrong ancestor). Repeats on the
  // next two frames because a turn's own follow-up state update (results
  // refresh) can re-layout and reset the container after the first scroll.
  // No pixel offsets — the sentinel's own geometry decides the position.
  const bottomSentinelRef = useRef<HTMLDivElement>(null)
  const turnsSignature = turns.map((t) => t.id).join('|')
  useEffect(() => {
    const scrollToBottom = () => {
      const sentinel = bottomSentinelRef.current
      if (!sentinel) return
      let scroller: HTMLElement | null = sentinel.parentElement
      while (scroller) {
        const style = window.getComputedStyle(scroller)
        if (/(auto|scroll)/.test(style.overflowY)) break
        scroller = scroller.parentElement
      }
      if (scroller) {
        scroller.scrollTop = scroller.scrollHeight
      } else {
        sentinel.scrollIntoView({ block: 'end' })
      }
    }
    let raf2 = 0
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(scrollToBottom)
    })
    const retry = window.setTimeout(scrollToBottom, 250)
    return () => {
      cancelAnimationFrame(raf1)
      cancelAnimationFrame(raf2)
      window.clearTimeout(retry)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [turnsSignature, loading])
  return (
    <ScrollArea className="h-full">
      <div className="space-y-4 p-4" aria-live="polite">
        {turns.length === 0 && !loading && (
          <div
            className="flex flex-col items-center gap-2 py-16 text-center text-sm text-muted-foreground"
            role="status"
          >
            <Bot className="h-6 w-6" aria-hidden />
            Start by asking for a case investigation, e.g. “Investigate U00299”.
          </div>
        )}

        {turns.map((turn) => (
          <Fragment key={turn.id}>
            {turn.role === 'user' ? (
              <div className="flex justify-end">
                <Card className="max-w-[85%] border-primary/20 bg-primary/5">
                  <CardContent className="flex items-start gap-2 p-3">
                    <User className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
                    <p className="text-sm">{turn.text}</p>
                  </CardContent>
                </Card>
              </div>
            ) : (
              <div className="flex justify-start">
                <Card className="max-w-[95%]">
                  <CardContent className="space-y-3 p-3">
                    <div className="flex items-center gap-2">
                      <Bot className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                      <span className="text-xs font-medium text-muted-foreground">
                        Investigation Agent
                      </span>
                    </div>
                    <p className="whitespace-pre-wrap text-sm leading-relaxed">
                      {renderTurnText(turn.text, focusedFindingId)}
                    </p>

                    {turn.timelineEvents && turn.timelineEvents.length > 0 && (
                      <div className="rounded-md border p-3">
                        <p className="mb-2 text-xs font-medium text-muted-foreground">
                          Timeline
                        </p>
                        <TimelineView
                          events={turn.timelineEvents}
                          focusedEventId={focusedEventId}
                          onSelect={onSelectEvent}
                        />
                      </div>
                    )}

                    {turn.plan && <PlanCard plan={turn.plan} />}
                    {turn.task && <TaskStatus task={turn.task} />}

                    {turn.artifacts && turn.artifacts.length > 0 && (
                      <div className="space-y-2">
                        {turn.artifacts.map((a) => (
                          <div
                            key={a.artifact_id}
                            className="flex items-center gap-2 rounded-md border p-2 text-xs"
                          >
                            <Badge variant="secondary">{a.artifact_type}</Badge>
                            <span className="font-medium">{a.title}</span>
                            <Badge variant="outline" className="ml-auto font-mono">
                              {a.artifact_id}
                            </Badge>
                          </div>
                        ))}
                      </div>
                    )}

                    {turn.execution && turn.execution.errors.length > 0 && (
                      <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive">
                        {turn.execution.errors.map((e, i) => (
                          <p key={i}>
                            {e.code}: {e.message}
                          </p>
                        ))}
                      </div>
                    )}

                    {turn.followUps && (
                      <FollowUpChips
                        followUps={turn.followUps}
                        onSelect={onSelectFollowUp}
                        onNavigate={onNavigate}
                        disabled={followUpsDisabled}
                        // Case Intake is a finding-level result: the natural
                        // next action is picking a finding (navigation, not
                        // an investigation intent — never submitted).
                        // Intake guidance reflects CURRENT focus state: with
                        // a focused finding it is no longer applicable and
                        // must not render (P12 — guidance derives from
                        // authoritative focus, not from message history).
                        navigationHint={
                          turn.task?.selected_skill === 'case_intake' &&
                          !focusedFindingId &&
                          turn.followUps.length > 0
                            ? 'Select a finding from the Findings panel on the left'
                            : undefined
                        }
                      />
                    )}
                  </CardContent>
                </Card>
              </div>
            )}
          </Fragment>
        ))}

        {/* Focus-confirmation messages are NORMAL conversation turns: they
            are appended to `turns` by the page when a selection is made and
            persist like any other historical message (they are never
            removed by later turns, follow-up clicks, or refresh). */}

        {loading && (
          <div className="flex justify-start" role="status" aria-label="Agent is working">
            <Card className="w-[70%]">
              <CardContent className="space-y-2 p-3">
                <div className="flex items-center gap-2">
                  <Bot className="h-4 w-4 text-primary" aria-hidden />
                  <span className="text-xs text-muted-foreground">
                    Resolving context, planning, and executing…
                  </span>
                </div>
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-4 w-1/2" />
              </CardContent>
            </Card>
          </div>
        )}

        {error && (
          <div role="alert">
            <Alert_ error={error} />
          </div>
        )}

        {/* Auto-scroll target: always the LAST element in the scroll
            content, after every turn / loading indicator / error. */}
        <div ref={bottomSentinelRef} aria-hidden="true" />
      </div>
    </ScrollArea>
  )
}

/** Bounded API error display — renders the server-provided message only. */
function Alert_({ error }: { error: string }) {
  return (
    <Card className="border-destructive/40 bg-destructive/5">
      <CardContent className="flex items-start gap-2 p-3">
        <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden />
        <div className="text-sm">
          <Badge variant="destructive" className="mb-1">
            Error
          </Badge>
          <p>{error}</p>
        </div>
      </CardContent>
    </Card>
  )
}
