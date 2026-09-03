import type { ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import type { Finding } from '@/api/types'
import { CheckCircle2, SearchX } from 'lucide-react'

interface FindingsPanelProps {
  findings: Finding[]
  focusedFindingId: string | null
  onSelect: (findingId: string) => void
  loading?: boolean
  /** Extra content rendered inside the findings scroll region (e.g. the
   * timeline card) so the whole lower area scrolls as one. */
  children?: ReactNode
}

/**
 * Compact finding-navigation surface (NOT an evidence viewer). Each card
 * shows exactly: finding ID, title, one concise explanation — nothing else.
 * Severity, evidence counts/IDs, signals, and policy citations are
 * deliberately absent: the conversation and artifacts are where evidence is
 * progressively investigated and (once reached) presented completely.
 */
export function FindingsPanel({
  findings,
  focusedFindingId,
  onSelect,
  loading = false,
  children,
}: FindingsPanelProps) {
  return (
    <Card className="flex h-full min-h-0 flex-col">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          Findings
          {findings.length > 0 && (
            <Badge variant="secondary">{findings.length}</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="min-h-0 flex-1 overflow-hidden p-0">
        <ScrollArea className="h-full px-4 pb-4" horizontal>
          {loading && (
            <div className="space-y-2" aria-label="Loading findings">
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-16 w-full" />
            </div>
          )}
          {!loading && findings.length === 0 && (
            <div
              className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground"
              role="status"
            >
              <SearchX className="h-5 w-5" aria-hidden />
              No findings yet. Run a case investigation to load them.
            </div>
          )}
          {!loading && (
            <ul className="min-w-full space-y-2" aria-label="Findings list">
              {findings.map((f) => {
                const selected = f.finding_id === focusedFindingId
                return (
                  <li key={f.finding_id}>
                    <Button
                      variant={selected ? 'secondary' : 'ghost'}
                      className={`h-auto w-full flex-col items-start gap-1 border px-3 py-2.5 text-left font-normal ${
                        selected
                          ? 'border-primary/50 bg-primary/5 ring-1 ring-primary/40'
                          : 'border-transparent'
                      }`}
                      aria-pressed={selected}
                      onClick={() => onSelect(f.finding_id)}
                    >
                      <span className="flex w-full items-center gap-2">
                        {selected && (
                          <CheckCircle2
                            className="h-3.5 w-3.5 shrink-0 text-primary"
                            aria-hidden
                          />
                        )}
                        <span className="font-mono text-xs text-muted-foreground">
                          {f.finding_id}
                        </span>
                      </span>
                      <span className="text-sm font-medium">{f.title}</span>
                      <span className="line-clamp-2 text-xs text-muted-foreground">
                        {f.summary}
                      </span>
                    </Button>
                  </li>
                )
              })}
            </ul>
          )}
          {children}
        </ScrollArea>
      </CardContent>
    </Card>
  )
}
