import { Step, StepStatus } from '@/types/task'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { StepStatusBadge } from '@/components/StepStatusBadge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { CheckCircle2, Circle, XCircle, Minus, Loader2, Sparkles, ListTree } from 'lucide-react'
import { cn } from '@/lib/utils'

interface PlanCardProps {
  steps: Step[]
  className?: string
}

export function PlanCard({ steps, className }: PlanCardProps) {
  const getStepIcon = (status: StepStatus) => {
    switch (status) {
      case 'success':
        return <CheckCircle2 className="h-4 w-4 text-green-500" />
      case 'failed':
        return <XCircle className="h-4 w-4 text-red-500" />
      case 'running':
        return <Loader2 className="h-4 w-4 text-blue-500 animate-spin" />
      case 'skipped':
        return <Minus className="h-4 w-4 text-gray-400" />
      default:
        return <Circle className="h-4 w-4 text-gray-300" />
    }
  }

  // Agent mode: No predefined plan
  if (steps.length === 0) {
    return (
      <Card className={cn(className)}>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Sparkles className="h-4 w-4" />
            Agent Investigation
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground space-y-2">
            <p>The agent will determine the execution steps dynamically when the investigation runs.</p>
            <p className="text-xs">Click "Run All" to start the agent investigation.</p>
          </div>
        </CardContent>
      </Card>
    )
  }

  // Agent execution: Show the actual agent-selected sequence
  const isAgentTrace = steps.some(s => s.name.includes('Agent Step'))

  return (
    <Card className={cn(className)}>
      <CardHeader>
        <CardTitle className="text-lg flex items-center gap-2">
          {isAgentTrace ? (
            <>
              <ListTree className="h-4 w-4" />
              Agent Execution Trace
            </>
          ) : (
            'Investigation Plan'
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ScrollArea className="h-[200px]">
          <div className="space-y-2">
            {steps.map((step, index) => (
              <div
                key={step.id}
                className="flex items-start gap-3 p-3 rounded-lg border bg-card hover:bg-accent/50 transition-colors"
              >
                <div className="flex items-center gap-2 mt-0.5">
                  {getStepIcon(step.status)}
                  <span className="text-xs text-muted-foreground font-mono">
                    {index + 1}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <h4 className="text-sm font-medium truncate">{step.name}</h4>
                    <StepStatusBadge status={step.status} />
                  </div>
                  {step.description && (
                    <p className="text-xs text-muted-foreground truncate">
                      {step.description}
                    </p>
                  )}
                  {step.tool_name && (
                    <code className="text-xs bg-muted px-1.5 py-0.5 rounded mt-1 inline-block">
                      {step.tool_name}
                    </code>
                  )}
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
      </CardContent>
    </Card>
  )
}
