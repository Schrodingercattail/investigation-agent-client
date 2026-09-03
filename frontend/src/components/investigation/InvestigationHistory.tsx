import { useState } from 'react'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ScrollArea } from '@/components/ui/scroll-area'
import type { InvestigationHistoryItem } from '@/api/types'
import { History, MoreHorizontal, Plus, Trash2 } from 'lucide-react'

function relativeTime(iso: string | null): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  const diff = Date.now() - then
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return new Date(iso).toLocaleDateString()
}

interface InvestigationHistoryProps {
  investigations: InvestigationHistoryItem[]
  currentInvestigationId: string | null
  onOpen: (investigationId: string) => void
  onDelete: (investigationId: string) => void
  onNewInvestigation: () => void
  disabled?: boolean
}

/**
 * ChatGPT-style history list: case + when it was updated. Operational task
 * status is deliberately NOT shown here — it lives in the Task UI inside
 * the current investigation. The frontend never ranks/searches/generates
 * titles — it renders what the API returned.
 */
export function InvestigationHistory({
  investigations,
  currentInvestigationId,
  onOpen,
  onDelete,
  onNewInvestigation,
  disabled,
}: InvestigationHistoryProps) {
  const [pendingDelete, setPendingDelete] =
    useState<InvestigationHistoryItem | null>(null)

  return (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <Button
        variant="default"
        className="w-full justify-start"
        disabled={disabled}
        onClick={onNewInvestigation}
      >
        <Plus className="h-4 w-4" aria-hidden />
        New Investigation
      </Button>
      <div className="flex items-center gap-2 px-1 text-xs font-medium text-muted-foreground">
        <History className="h-3.5 w-3.5" aria-hidden />
        History
      </div>
      <ScrollArea className="min-h-0 flex-1">
        {investigations.length === 0 ? (
          <p className="px-1 py-4 text-xs text-muted-foreground" role="status">
            No previous investigations.
          </p>
        ) : (
          <ul className="space-y-1 pr-2" aria-label="Investigation history">
            {investigations.map((inv) => {
              const isCurrent = inv.investigation_id === currentInvestigationId
              return (
                <li key={inv.investigation_id} className="relative">
                  <Button
                    variant={isCurrent ? 'secondary' : 'ghost'}
                    className={`h-auto w-full flex-col items-start gap-0.5 border px-2.5 py-2 text-left font-normal ${
                      isCurrent
                        ? 'border-primary/50 ring-1 ring-primary/40'
                        : 'border-transparent'
                    }`}
                    aria-pressed={isCurrent}
                    disabled={disabled}
                    onClick={() => onOpen(inv.investigation_id)}
                  >
                    <span className="text-sm font-medium">
                      Investigation · {inv.case_id}
                    </span>
                    <span className="flex w-full items-center gap-2 text-[10px] text-muted-foreground">
                      <span className="font-mono">{inv.investigation_id}</span>
                      <span className="ml-auto">
                        Updated {relativeTime(inv.updated_at)}
                      </span>
                    </span>
                  </Button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="absolute right-1 top-1 h-6 w-6 p-0"
                        disabled={disabled}
                        aria-label={`Actions for investigation ${inv.case_id}`}
                      >
                        <MoreHorizontal className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem
                        className="text-destructive focus:text-destructive"
                        onSelect={() => setPendingDelete(inv)}
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                        Delete investigation
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </li>
              )
            })}
          </ul>
        )}
      </ScrollArea>

      {/* explicit confirmation before deletion */}
      <AlertDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete investigation {pendingDelete?.case_id}?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This removes the investigation and its stored history. This
              action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                if (pendingDelete) onDelete(pendingDelete.investigation_id)
                setPendingDelete(null)
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
