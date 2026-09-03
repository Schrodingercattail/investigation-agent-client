import { Badge } from '@/components/ui/badge'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import { Card, CardContent } from '@/components/ui/card'
import type { PlanStep, PlanSummary } from '@/api/types'
import {
  Circle,
  CircleCheck,
  CircleDashed,
  CircleX,
  MinusCircle,
} from 'lucide-react'

type PlanStepStatus = PlanStep['status']

/** Step status → icon + label. Pure presentation of API state — the
 * frontend never infers or modifies step status. */
function StepStatus({ status }: { status: PlanStepStatus }) {
  const cls = 'h-3.5 w-3.5 shrink-0'
  switch (status) {
    case 'success':
      return (
        <>
          <CircleCheck className={`${cls} text-green-600`} aria-hidden />
          <span className="sr-only">completed</span>
        </>
      )
    case 'failed':
      return (
        <>
          <CircleX className={`${cls} text-destructive`} aria-hidden />
          <span className="sr-only">failed</span>
        </>
      )
    case 'running':
      return (
        <>
          <CircleDashed className={`${cls} animate-spin text-primary`} aria-hidden />
          <span className="sr-only">running</span>
        </>
      )
    case 'skipped':
      return (
        <>
          <MinusCircle className={`${cls} text-muted-foreground`} aria-hidden />
          <span className="sr-only">skipped</span>
        </>
      )
    case 'rejected':
      return (
        <>
          <CircleX className={`${cls} text-destructive`} aria-hidden />
          <span className="sr-only">rejected</span>
        </>
      )
    default:
      return (
        <>
          <Circle className={`${cls} text-muted-foreground`} aria-hidden />
          <span className="sr-only">pending</span>
        </>
      )
  }
}

interface PlanCardProps {
  plan: PlanSummary | null
}

export function PlanCard({ plan }: PlanCardProps) {
  if (!plan) return null
  return (
    <Card>
      <CardContent className="p-0">
        <Accordion type="single" collapsible>
          <AccordionItem value="plan" className="border-none">
            <AccordionTrigger className="px-4 py-3 hover:no-underline">
              <span className="flex w-full items-center gap-2 pr-2 text-left">
                <span className="text-sm font-semibold">Plan</span>
                <Badge variant="outline" className="font-mono text-[10px]">
                  {plan.selected_skill ?? '—'}
                </Badge>
                <Badge variant="secondary" className="ml-auto">
                  {plan.steps.filter((s) => s.status === 'success').length}/
                  {plan.steps.length}
                </Badge>
              </span>
            </AccordionTrigger>
            <AccordionContent className="px-4 pb-3">
              <p className="mb-2 text-xs text-muted-foreground">{plan.goal}</p>
              <ol className="space-y-1.5">
                {plan.steps.map((step) => (
                  <li key={step.step_id} className="flex items-start gap-2 text-sm">
                    <span className="mt-0.5">
                      <StepStatus status={step.status} />
                    </span>
                    <span
                      className={
                        step.status === 'failed'
                          ? 'text-destructive'
                          : undefined
                      }
                    >
                      {step.tool_name ?? step.type}
                      {step.error && (
                        <span className="block text-xs text-muted-foreground">
                          {step.error}
                        </span>
                      )}
                    </span>
                  </li>
                ))}
              </ol>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </CardContent>
    </Card>
  )
}
