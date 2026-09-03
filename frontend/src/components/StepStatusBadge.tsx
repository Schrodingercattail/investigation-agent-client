import { Badge } from '@/components/ui/badge'
import { StepStatus } from '@/types/task'
import { cn } from '@/lib/utils'

interface StepStatusBadgeProps {
  status: StepStatus
  className?: string
}

const statusConfig: Record<StepStatus, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' }> = {
  pending: { label: 'Pending', variant: 'secondary' },
  running: { label: 'Running', variant: 'default' },
  success: { label: 'Success', variant: 'default' },
  failed: { label: 'Failed', variant: 'destructive' },
  skipped: { label: 'Skipped', variant: 'outline' },
}

export function StepStatusBadge({ status, className }: StepStatusBadgeProps) {
  const config = statusConfig[status]

  return (
    <Badge variant={config.variant} className={cn('capitalize', className)}>
      {config.label}
    </Badge>
  )
}
