import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import type { Task } from '@/api/types'
import { AlertCircle, CheckCircle2, Loader2, Octagon, CircleDashed } from 'lucide-react'

/** Task status → Badge variant + icon. Pure presentation of API state. */
function statusVariant(status: Task['status']) {
  switch (status) {
    case 'completed':
      return 'default' as const
    case 'failed':
    case 'cancelled':
      return 'destructive' as const
    case 'planning':
    case 'executing':
      return 'secondary' as const
    default:
      return 'outline' as const
  }
}

function StatusIcon({ status }: { status: Task['status'] }) {
  const cls = 'h-3.5 w-3.5'
  switch (status) {
    case 'completed':
      return <CheckCircle2 className={`${cls} text-green-600`} aria-hidden />
    case 'failed':
      return <AlertCircle className={`${cls} text-destructive`} aria-hidden />
    case 'cancelled':
      return <Octagon className={`${cls} text-destructive`} aria-hidden />
    case 'planning':
    case 'executing':
      return <Loader2 className={`${cls} animate-spin text-primary`} aria-hidden />
    default:
      return <CircleDashed className={`${cls} text-muted-foreground`} aria-hidden />
  }
}

interface TaskStatusProps {
  task: Task | null
}

export function TaskStatus({ task }: TaskStatusProps) {
  if (!task) return null

  const inFlight =
    task.status === 'pending' || task.status === 'planning' || task.status === 'executing'

  return (
    <Card>
      <CardContent className="flex flex-col gap-2 p-3">
        <div className="flex items-center gap-2">
          <StatusIcon status={task.status} />
          <span className="text-sm font-medium">Task</span>
          <Badge variant={statusVariant(task.status)} className="ml-auto">
            {task.status}
          </Badge>
        </div>
        <p className="truncate text-xs text-muted-foreground" title={task.user_request}>
          {task.user_request}
        </p>
        {task.selected_skill && (
          <p className="font-mono text-[10px] text-muted-foreground">
            skill: {task.selected_skill}
          </p>
        )}
        {task.status === 'failed' && task.error && (
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" aria-hidden />
            <AlertTitle>Investigation step failed</AlertTitle>
            <AlertDescription className="text-xs">
              {/* bounded backend error only — never a stack trace */}
              {task.error}
            </AlertDescription>
          </Alert>
        )}
        {inFlight && (
          <div
            className="h-1 w-full overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-label="Task in progress"
          >
            <div className="h-full w-1/3 animate-pulse rounded-full bg-primary" />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
