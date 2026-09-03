import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import type { Finding, InvestigationContext } from '@/api/types'
import { Crosshair, Focus, Info, X } from 'lucide-react'

/** Human-readable focus source (backend semantics → user language). */
function focusSourceLabel(source: InvestigationContext['focus_source']) {
  switch (source) {
    case 'user_selected':
      return 'Selected by you'
    case 'agent_resolved':
      return 'Resolved from conversation'
    case 'system_default':
      return 'System default'
    default:
      return null
  }
}

interface ContextPanelProps {
  context: InvestigationContext
  /** Case findings — used to show the focused finding's title, not just its
   * id (pure presentation lookup; the backend owns the entities). */
  findings?: Finding[]
  onClearFocus?: () => void
}

export function ContextPanel({ context, findings = [], onClearFocus }: ContextPanelProps) {
  const hasFocus =
    context.focused_finding_id !== null || context.focused_event_id !== null
  const sourceLabel = focusSourceLabel(context.focus_source)
  // presentation-only title lookup: never shown as a bare "none"
  const focusedFinding = findings.find(
    (f) => f.finding_id === context.focused_finding_id,
  )

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Focus className="h-4 w-4" aria-hidden />
          Context
        </CardTitle>
        {hasFocus && onClearFocus && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onClearFocus}
            aria-label="Clear focus"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
            Clear
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div className="flex items-center justify-between gap-2">
          <span className="text-muted-foreground">Case</span>
          <span className="font-mono text-xs">{context.case_id}</span>
        </div>
        <Separator />
        <div>
          <span className="text-xs text-muted-foreground">Focus</span>
          {context.focused_finding_id ? (
            <p className="mt-0.5 flex items-center gap-1.5 font-medium">
              <Crosshair className="h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
              <span>
                {context.focused_finding_id}
                {focusedFinding ? ` — ${focusedFinding.title}` : ''}
              </span>
            </p>
          ) : (
            <p className="mt-0.5 font-medium">Case overview</p>
          )}
          {!context.focused_finding_id && (
            <p className="mt-1 text-xs text-muted-foreground">
              Select a finding to investigate further.
            </p>
          )}
        </div>
        {context.focused_event_id && (
          <>
            <Separator />
            <div>
              <span className="text-xs text-muted-foreground">Event</span>
              <p className="mt-0.5 font-mono text-xs font-medium">
                {context.focused_event_id}
              </p>
            </div>
          </>
        )}
        {hasFocus && (
          <>
            <Separator />
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-muted-foreground">
                Source
                <TooltipProvider delayDuration={200}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Info
                        className="h-3.5 w-3.5"
                        aria-label="How the current focus was established"
                      />
                    </TooltipTrigger>
                    <TooltipContent>
                      How the current focus was established.
                    </TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </span>
              <span className="text-xs">{sourceLabel ?? '—'}</span>
            </div>
          </>
        )}
        {context.time_window && (
          <>
            <Separator />
            <div className="flex items-center justify-between gap-2">
              <span className="text-muted-foreground">Time window</span>
              <span className="text-xs">{context.time_window}</span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
