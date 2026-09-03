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
import { Fragment } from 'react'
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
  /** Presentation-only selection acknowledgment ("You're now investigating
   * F3 — …") shown after the persisted turns while the selection awaits its
   * next turn; its follow-ups are the backend-previewed set. */
  acknowledgment?: ConversationTurn | null
}

/**
 * Chat-style conversation workspace. Structured attachments (plan,
 * execution, artifacts, follow-ups) are visually distinct from natural
 * language. Everything rendered comes from the API response.
 */
export function ConversationPanel({
  turns,
  loading,
  error,
  onSelectEvent,
  focusedEventId,
  onSelectFollowUp,
  onNavigate,
  followUpsDisabled,
  acknowledgment,
}: ConversationPanelProps) {
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
                      {turn.text}
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
                        navigationHint={
                          turn.task?.selected_skill === 'case_intake' &&
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

        {/* Selection acknowledgment: presentation-only Agent message that
            reflects the just-made selection and carries the backend-
            previewed Suggested Follow-ups at its bottom. It is NOT an
            investigation result — no plan/task metadata is attached. */}
        {acknowledgment && (
          <div className="flex justify-start">
            <Card className="max-w-[95%] border-primary/30">
              <CardContent className="space-y-3 p-3">
                <div className="flex items-center gap-2">
                  <Bot className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                  <span className="text-xs font-medium text-muted-foreground">
                    Investigation Agent
                  </span>
                </div>
                <p className="whitespace-pre-wrap text-sm leading-relaxed">
                  {acknowledgment.text}
                </p>
                {acknowledgment.followUps && (
                  <FollowUpChips
                    followUps={acknowledgment.followUps}
                    onSelect={onSelectFollowUp}
                    onNavigate={onNavigate}
                    disabled={followUpsDisabled}
                  />
                )}
              </CardContent>
            </Card>
          </div>
        )}

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
