import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import { Crosshair, Clock } from 'lucide-react'

export interface TimelineEventData {
  event_id: string
  finding_id: string
  timestamp: string
  event_type: string
  summary: string
  importance: string | null
  evidence_refs: { kind: string; id: string }[]
}

interface TimelineViewProps {
  events: TimelineEventData[]
  focusedEventId: string | null
  onSelect: (eventId: string) => void
}

/**
 * Vertical timeline composed from shadcn primitives. Events come from the
 * backend timeline result; selecting one sends the supported focus_event
 * context action on the next turn — it never invokes a tool directly.
 * Presentation shows investigation-useful fields only (time, type, summary,
 * evidence refs); unexplained importance/severity labels are not shown.
 */
export function TimelineView({ events, focusedEventId, onSelect }: TimelineViewProps) {
  if (events.length === 0) {
    return (
      <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground" role="status">
        No timeline events in this result.
      </p>
    )
  }

  return (
    <ol className="space-y-3" aria-label="Timeline">
      {events.map((ev, idx) => {
        const selected = ev.event_id === focusedEventId
        return (
          <li key={ev.event_id} className="relative flex gap-3">
            {/* rail */}
            <div className="flex flex-col items-center pt-1">
              <span
                className={cn(
                  'h-2.5 w-2.5 rounded-full border-2',
                  selected ? 'border-primary bg-primary' : 'border-muted-foreground/40 bg-background',
                )}
                aria-hidden
              />
              {idx < events.length - 1 && (
                <span className="mt-1 w-px flex-1 bg-border" aria-hidden />
              )}
            </div>
            <Card
              className={cn(
                'mb-1 w-full',
                selected && 'border-primary/50 ring-1 ring-primary/40',
              )}
            >
              <CardContent className="space-y-1.5 p-3">
                <div className="flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="flex items-center gap-1 text-muted-foreground">
                    <Clock className="h-3 w-3" aria-hidden />
                    {ev.timestamp}
                  </span>
                  <Badge variant="outline" className="font-mono text-[10px]">
                    {ev.event_type}
                  </Badge>
                </div>
                <p className="text-sm">{ev.summary}</p>
                {ev.evidence_refs.length > 0 && (
                  <>
                    <Separator />
                    <p className="flex flex-wrap gap-1 text-[10px] text-muted-foreground">
                      Evidence:
                      {ev.evidence_refs.map((r) => (
                        <span key={`${r.kind}:${r.id}`} className="font-mono">
                          {r.kind}:{r.id}
                        </span>
                      ))}
                    </p>
                  </>
                )}
                <Button
                  variant={selected ? 'secondary' : 'ghost'}
                  size="sm"
                  className="h-7 px-2 text-xs"
                  aria-pressed={selected}
                  onClick={() => onSelect(ev.event_id)}
                >
                  <Crosshair className="h-3 w-3" aria-hidden />
                  {selected ? 'Focused' : 'Focus this event'}
                </Button>
              </CardContent>
            </Card>
          </li>
        )
      })}
    </ol>
  )
}
