import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import type { FollowUp } from '@/api/types'
import { ArrowUpRight, MousePointerClick } from 'lucide-react'

interface FollowUpChipsProps {
  followUps: FollowUp[]
  onSelect: (followUp: FollowUp) => void
  /** Handler for ui_navigation follow-ups (e.g. focus the Artifacts
   * panel). Pure UI action — no Agent turn is created. */
  onNavigate?: (followUp: FollowUp) => void
  disabled?: boolean
  /** Prompt shown above the chips (e.g. inside a turn card vs. the
   * workspace action bar). */
  label?: string
  /** Omit the separator/top margin (used when embedded in a bordered bar). */
  embedded?: boolean
  /** Purely navigational guidance line (no follow-up semantics, not
   * clickable as an investigation intent — e.g. "Select a finding from
   * the Findings panel" after intake). Rendered first. */
  navigationHint?: string
}

/**
 * Renders backend-provided Suggested Follow-ups as action chips.
 *
 * The frontend does not generate, filter, or map follow-ups to tools —
 * it renders exactly what the API returned, and a click submits a new
 * normal turn using the backend follow_up_id mechanism. A purely
 * navigational hint (navigationHint) is presentation-level guidance, never
 * submitted as an intent.
 */
export function FollowUpChips({
  followUps,
  onSelect,
  onNavigate,
  disabled,
  label = 'Suggested next steps',
  embedded = false,
  navigationHint,
}: FollowUpChipsProps) {
  if (followUps.length === 0 && !navigationHint) return null

  return (
    <div className={embedded ? undefined : 'mt-2'} role="group" aria-label="Suggested follow-ups">
      {!embedded && <Separator className="mb-2" />}
      <p className="mb-1.5 text-xs text-muted-foreground">{label}</p>
      <div className="flex flex-wrap gap-2">
        {navigationHint && (
          <span
            className="inline-flex items-center gap-1.5 rounded-md border border-dashed px-3 py-1.5 text-sm text-muted-foreground"
            data-testid="navigation-hint"
          >
            <MousePointerClick className="h-3.5 w-3.5" aria-hidden />
            {navigationHint}
          </span>
        )}
        {followUps.map((f) => (
          <Button
            key={f.follow_up_id}
            variant="outline"
            size="sm"
            disabled={disabled}
            title={f.reason ?? f.label}
            aria-label={`Suggested follow-up: ${f.label}`}
            onClick={() => {
              // P13: navigation actions never enter the Agent pipeline.
              if (f.action_kind === 'ui_navigation') {
                onNavigate?.(f)
                return
              }
              onSelect(f)
            }}
          >
            {f.label}
            <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
          </Button>
        ))}
      </div>
    </div>
  )
}
