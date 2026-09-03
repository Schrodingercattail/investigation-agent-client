import { useState, useEffect } from 'react'
import { Task } from '@/types/task'
import { TaskStatus } from '@/types/task'
import { api, ApiError, RateLimitError, ServiceUnavailableError, NetworkError, ValidationError } from '@/lib/api'
import { PlanCard } from '@/components/PlanCard'
import { RunLog } from '@/components/RunLog'
import { ArtifactPanel } from '@/components/ArtifactPanel'
import { ChatInput } from '@/components/ChatInput'
import { TaskStatusBadge } from '@/components/TaskStatusBadge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Progress } from '@/components/ui/progress'
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from '@/components/ui/alert-dialog'
import { Loader2, Play, AlertCircle, Plus, RefreshCw, WifiOff, Clock, Server } from 'lucide-react'

const CURRENT_TASK_STORAGE_KEY = 'investigation-agent.current_task_id'

interface ErrorDisplay {
  message: string
  type: 'error' | 'warning' | 'info'
  retryable: boolean
  icon?: 'wifi-off' | 'clock' | 'server' | 'alert-circle'
}

export function ChatPage() {
  const [task, setTask] = useState<Task | null>(null)
  const [loading, setLoading] = useState(false)
  const [errorDisplay, setErrorDisplay] = useState<ErrorDisplay | null>(null)
  const [isRestoring, setIsRestoring] = useState(true)
  const [regenerating, setRegenerating] = useState(false)

  // Helper to convert ApiError to ErrorDisplay
  const getErrorDisplay = (error: unknown): ErrorDisplay => {
    if (error instanceof ValidationError) {
      return {
        message: error.message,
        type: 'warning',
        retryable: false,
        icon: 'alert-circle',
      }
    }
    if (error instanceof RateLimitError) {
      return {
        message: 'Agent rate limit exceeded. Please wait a moment and try again.',
        type: 'warning',
        retryable: true,
        icon: 'clock',
      }
    }
    if (error instanceof NetworkError) {
      return {
        message: 'Network error. Please check your connection and try again.',
        type: 'error',
        retryable: true,
        icon: 'wifi-off',
      }
    }
    if (error instanceof ServiceUnavailableError) {
      return {
        message: 'Service temporarily unavailable. Please try again later.',
        type: 'warning',
        retryable: true,
        icon: 'server',
      }
    }
    if (error instanceof ApiError) {
      return {
        message: error.message,
        type: 'error',
        retryable: false,
        icon: 'alert-circle',
      }
    }
    // Generic error
    return {
      message: error instanceof Error ? error.message : 'An unexpected error occurred',
      type: 'error',
      retryable: false,
      icon: 'alert-circle',
    }
  }

  const getErrorIcon = (icon?: string) => {
    switch (icon) {
      case 'wifi-off':
        return <WifiOff className="h-4 w-4" />
      case 'clock':
        return <Clock className="h-4 w-4" />
      case 'server':
        return <Server className="h-4 w-4" />
      default:
        return <AlertCircle className="h-4 w-4" />
    }
  }

  // Restore current task on mount
  useEffect(() => {
    const restoreTask = async () => {
      const currentTaskId = localStorage.getItem(CURRENT_TASK_STORAGE_KEY)
      if (currentTaskId) {
        try {
          const restoredTask = await api.getTask(currentTaskId)
          setTask(restoredTask)
        } catch (err) {
          // Task might have been deleted or backend unavailable
          console.error('Failed to restore task:', err)
          setErrorDisplay(getErrorDisplay(err))
          localStorage.removeItem(CURRENT_TASK_STORAGE_KEY)
        }
      }
      setIsRestoring(false)
    }
    restoreTask()
  }, [])

  const handleCreateTask = async (userIntent: string) => {
    setLoading(true)
    setErrorDisplay(null)

    try {
      const newTask = await api.createTask({ user_intent: userIntent })
      setTask(newTask)
      // Store task ID for restoration
      localStorage.setItem(CURRENT_TASK_STORAGE_KEY, newTask.id)
    } catch (err) {
      setErrorDisplay(getErrorDisplay(err))
    } finally {
      setLoading(false)
    }
  }

  const handleRunAll = async () => {
    if (!task) return

    setLoading(true)
    setErrorDisplay(null)

    try {
      // Update local state to running immediately
      setTask({ ...task, status: TaskStatus.RUNNING })

      const updatedTask = await api.runTask(task.id)
      setTask(updatedTask)
    } catch (err) {
      setErrorDisplay(getErrorDisplay(err))
      // Revert status on error
      setTask(task)
    } finally {
      setLoading(false)
    }
  }

  const handleNewInvestigation = () => {
    setTask(null)
    setErrorDisplay(null)
    // Clear stored task ID
    localStorage.removeItem(CURRENT_TASK_STORAGE_KEY)
  }

  const handleRegenerate = async () => {
    if (!task) return

    setRegenerating(true)
    setErrorDisplay(null)

    try {
      // Update local state to running immediately
      setTask({ ...task, status: TaskStatus.RUNNING })

      const updatedTask = await api.regenerateTask(task.id)
      setTask(updatedTask)
    } catch (err) {
      setErrorDisplay(getErrorDisplay(err))
      // Revert status on error
      setTask(task)
    } finally {
      setRegenerating(false)
    }
  }

  const handleRetry = () => {
    // Retry the last operation based on current state
    if (task && task.status === TaskStatus.READY) {
      handleRunAll()
    } else if (!task) {
      // Can't retry task creation without the original intent
      setErrorDisplay({
        message: 'Cannot retry. Please enter your investigation request again.',
        type: 'info',
        retryable: false,
      })
    }
  }

  const getProgress = () => {
    if (!task || task.steps.length === 0) return 0
    const completed = task.steps.filter(
      (s) => s.status === 'success' || s.status === 'failed'
    ).length
    return (completed / task.steps.length) * 100
  }

  return (
    <div className="flex flex-col min-h-screen bg-background">
      {/* Header */}
      <header className="border-b bg-card shrink-0">
        <div className="container mx-auto px-4 py-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center justify-between w-full">
              {/* Left side: Title and status */}
              <div>
                <h1 className="text-xl font-semibold">Investigation Agent</h1>
                {task && (
                  <div className="flex items-center gap-2 mt-1">
                    <TaskStatusBadge status={task.status} />
                    {task.case_id && (
                      <span className="text-xs font-mono bg-primary/10 text-primary px-2 py-0.5 rounded">
                        {task.case_id}
                      </span>
                    )}
                    {task.execution_mode && (
                      <span className="text-xs text-muted-foreground">
                        Mode: {task.execution_mode}
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* Right side: Action buttons */}
              <div className="flex items-center gap-2">
                {task && task.status === TaskStatus.COMPLETED && (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-muted-foreground"
                        disabled={regenerating || loading}
                      >
                        <RefreshCw className={`h-4 w-4 mr-1 ${regenerating ? 'animate-spin' : ''}`} />
                        <span className="hidden sm:inline">Regenerate</span>
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Regenerate investigation?</AlertDialogTitle>
                        <AlertDialogDescription>
                          This will run the agent again and replace the current investigation result with a new execution.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={handleRegenerate}>
                          Regenerate
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                )}
                {task && (
                  <Button
                    onClick={handleNewInvestigation}
                    variant="ghost"
                    size="sm"
                    className="text-muted-foreground"
                  >
                    <Plus className="h-4 w-4 mr-1" />
                    <span className="hidden sm:inline">New Investigation</span>
                  </Button>
                )}
              </div>
            </div>
            {task && task.status === TaskStatus.READY && (
              <Button onClick={handleRunAll} disabled={loading} size="sm">
                {loading ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Running...
                  </>
                ) : (
                  <>
                    <Play className="h-4 w-4 mr-2" />
                    Run All
                  </>
                )}
              </Button>
            )}
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1">
        <div className="container mx-auto px-4 py-4">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* Left/ Main Column */}
            <div className="lg:col-span-2 flex flex-col gap-4">
              {/* Error Alert with type-specific styling */}
              {errorDisplay && (
                <Alert variant={errorDisplay.type === 'error' ? 'destructive' : 'default'}>
                  {getErrorIcon(errorDisplay.icon)}
                  <AlertDescription className="flex items-center justify-between gap-2">
                    <span>{errorDisplay.message}</span>
                    {errorDisplay.retryable && (
                      <Button
                        variant={errorDisplay.type === 'error' ? 'outline' : 'ghost'}
                        size="sm"
                        onClick={handleRetry}
                        className="shrink-0"
                      >
                        Retry
                      </Button>
                    )}
                  </AlertDescription>
                </Alert>
              )}

              {/* Progress Bar when running */}
              {(task && task.status === TaskStatus.RUNNING && loading) && (
                <Card>
                  <CardContent className="pt-6">
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium">Running Investigation...</span>
                        <span className="text-muted-foreground">
                          {Math.round(getProgress())}%
                        </span>
                      </div>
                      <Progress value={getProgress()} />
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Progress Bar when regenerating */}
              {regenerating && (
                <Card>
                  <CardContent className="pt-6">
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-sm">
                        <span className="font-medium">Regenerating investigation...</span>
                        <span className="text-muted-foreground">
                          {Math.round(getProgress())}%
                        </span>
                      </div>
                      <Progress value={getProgress()} />
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Empty State with Input */}
              {!task && !loading && !isRestoring && (
                <div className="flex items-center justify-center min-h-[400px]">
                  <div className="w-full max-w-2xl space-y-4 text-center">
                    <h2 className="text-2xl font-semibold">Start an Investigation</h2>
                    <p className="text-muted-foreground">
                      Enter a case to investigate, for example: <span className="font-mono text-sm">U00299</span>
                    </p>
                    <ChatInput
                      onSubmit={handleCreateTask}
                      disabled={loading}
                      loading={loading}
                    />
                  </div>
                </div>
              )}

              {/* Restoration Loading State */}
              {isRestoring && (
                <div className="flex items-center justify-center min-h-[400px]">
                  <div className="text-center space-y-4">
                    <Loader2 className="h-8 w-8 animate-spin mx-auto text-muted-foreground" />
                    <p className="text-sm text-muted-foreground">Restoring investigation...</p>
                  </div>
                </div>
              )}

              {/* Task Content */}
              {task && (
                <>
                  {/* Plan Card */}
                  <PlanCard steps={task.steps} />

                  {/* Run Log */}
                  <RunLog steps={task.steps} />

                  {/* Chat Input */}
                  <div className="border-t bg-card p-4">
                    <ChatInput
                      onSubmit={handleCreateTask}
                      disabled={loading || task.status === TaskStatus.RUNNING}
                      placeholder="Start a new investigation..."
                    />
                  </div>
                </>
              )}
            </div>

            {/* Right/ Artifact Column */}
            <div>
              <ArtifactPanel artifacts={task?.artifacts || []} />
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
