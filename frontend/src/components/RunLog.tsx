import { Step, StepStatus } from '@/types/task'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { ChevronRight, Play, Clock, AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useState } from 'react'

const JSONViewer = ({ data, maxHeight = '300px' }: { data: any, maxHeight?: string }) => {
  return (
    <div
      className="mt-1 rounded border bg-background"
      style={{ position: 'relative', maxWidth: '100%' }}
    >
      <div
        style={{
          maxHeight: maxHeight,
          overflowX: 'auto',
          overflowY: 'auto',
          WebkitOverflowScrolling: 'touch',
          maxWidth: '100%'
        }}
      >
        <pre
          style={{
            fontSize: '12px',
            padding: '8px',
            whiteSpace: 'pre',
            margin: '0',
            width: 'max-content',
            maxWidth: 'none'
          }}
        >
          {JSON.stringify(data, null, 2)}
        </pre>
      </div>
    </div>
  )
}

interface RunLogProps {
  steps: Step[]
  className?: string
  maxContentHeight?: string
}

export function RunLog({ steps, className, maxContentHeight = '600px' }: RunLogProps) {
  const [openItems, setOpenItems] = useState<Set<string>>(new Set())

  const toggleOpen = (id: string) => {
    const newOpen = new Set(openItems)
    if (newOpen.has(id)) {
      newOpen.delete(id)
    } else {
      newOpen.add(id)
    }
    setOpenItems(newOpen)
  }

  const executedSteps = steps.filter(
    (step) => step.status === StepStatus.SUCCESS || step.status === StepStatus.FAILED
  )

  // No execution yet - show appropriate message
  if (executedSteps.length === 0) {
    return (
      <Card className={cn(className)} style={{ overflow: 'visible' }}>
        <CardHeader>
          <CardTitle className="text-lg">Execution Log</CardTitle>
        </CardHeader>
        <CardContent className="p-4">
          <div className="text-sm text-muted-foreground space-y-2">
            <p>No execution yet — run the investigation to see tool activity.</p>
            {steps.length > 0 && (
              <p className="text-xs">{steps.length} step(s) pending execution.</p>
            )}
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className={cn(className)} style={{ overflow: 'visible' }}>
      <CardHeader>
        <CardTitle className="text-lg">Execution Log</CardTitle>
      </CardHeader>
      <CardContent className="p-0" style={{ overflow: 'visible' }}>
        <div style={{ height: maxContentHeight, overflowY: 'auto' }}>
          <div className="p-4">
            <div className="space-y-2 pr-4">
              {executedSteps.map((step) => {
              const isOpen = openItems.has(step.id)
              const toolCall = step.tool_call
              const hasError = step.status === StepStatus.FAILED || toolCall?.status === 'failed'

              return (
                <Collapsible
                  key={step.id}
                  open={isOpen}
                  onOpenChange={() => toggleOpen(step.id)}
                >
                  <div className="border rounded-lg">
                    <CollapsibleTrigger asChild>
                      <Button
                        variant="ghost"
                        className="w-full flex items-center justify-between p-3 hover:bg-accent"
                      >
                        <div className="flex items-center gap-2 flex-1 text-left">
                          {hasError ? (
                            <AlertCircle className="h-4 w-4 text-red-500" />
                          ) : (
                            <Play className="h-4 w-4 text-green-500" />
                          )}
                          <span className="text-sm font-medium">
                            {toolCall?.tool_name || step.name}
                          </span>
                          {toolCall?.latency_ms && (
                            <div className="flex items-center gap-1 text-xs text-muted-foreground">
                              <Clock className="h-3 w-3" />
                              <span>{toolCall.latency_ms}ms</span>
                            </div>
                          )}
                        </div>
                        <ChevronRight
                          className={cn(
                            'h-4 w-4 transition-transform',
                            isOpen && 'rotate-90'
                          )}
                        />
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent>
                      <div className="p-3 border-t bg-muted/50 space-y-3">
                        {/* Tool Call Info */}
                        {toolCall ? (
                          <div className="space-y-2">
                            <div className="text-xs">
                              <span className="font-medium">Status:</span>{' '}
                              <span
                                className={cn(
                                  step.status === 'success'
                                    ? 'text-green-600'
                                    : 'text-red-600'
                                )}
                              >
                                {String(toolCall.status)}
                              </span>
                            </div>
                            {toolCall.args && Object.keys(toolCall.args).length > 0 && (
                              <div>
                                <span className="text-xs font-medium">Arguments:</span>
                                <JSONViewer data={toolCall.args} maxHeight="200px" />
                              </div>
                            )}
                            {toolCall.output_summary && (
                              <div className="text-xs">
                                <span className="font-medium">Summary:</span>{' '}
                                <span className="break-words">{String(toolCall.output_summary)}</span>
                              </div>
                            )}
                          </div>
                        ) : null}

                        {/* Output Preview */}
                        {toolCall?.output && typeof toolCall.output === 'object' ? (
                          <div>
                            <span className="text-xs font-medium">Output:</span>
                            <JSONViewer data={toolCall.output} maxHeight="300px" />
                          </div>
                        ) : toolCall?.output ? (
                          <div>
                            <span className="text-xs font-medium">Output:</span>
                            <div className="mt-1 rounded border bg-background">
                              <div
                                style={{
                                  maxHeight: '300px',
                                  overflowX: 'auto',
                                  overflowY: 'auto',
                                  WebkitOverflowScrolling: 'touch'
                                }}
                              >
                                <pre
                                  style={{
                                    fontSize: '0.75rem',
                                    padding: '0.5rem',
                                    whiteSpace: 'pre',
                                    width: 'max-content',
                                    minWidth: '100%',
                                    boxSizing: 'border-box'
                                  }}
                                >
                                  {String(toolCall.output)}
                                </pre>
                              </div>
                            </div>
                          </div>
                        ) : null}
                      </div>
                    </CollapsibleContent>
                  </div>
                </Collapsible>
              )
            })}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
