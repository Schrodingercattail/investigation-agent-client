import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import type { Artifact } from '@/api/types'
import { FileText, Maximize2, X } from 'lucide-react'
import { useState } from 'react'

interface ArtifactsPanelProps {
  artifacts: Artifact[]
  /** Expanded inline render (conversation column). */
  expandedId?: string | null
  onCollapse?: () => void
}

/**
 * Renders backend-provided Markdown artifacts. Week 1: format="md" only —
 * content is rendered as preformatted text in a scroll area (a rich Markdown
 * renderer can replace this without changing the contract). The frontend
 * never generates artifacts and never fabricates download URLs.
 */
export function ArtifactsPanel({ artifacts, expandedId, onCollapse }: ArtifactsPanelProps) {
  const [activeTab, setActiveTab] = useState<string | undefined>(undefined)

  // Exactly ONE active artifact at a time. Radix Tabs values must be
  // UNIQUE: the backend derives artifact IDs from content (sha256), so a
  // re-generated identical bundle legitimately repeats the same ID. Tab
  // values are therefore index-qualified ("idx:<n>:<artifact_id>") —
  // unique per position, stable for the list being rendered — while the
  // artifact identity stays in the key. A stale selection that no longer
  // matches any slot falls back to the first artifact.
  const tabValue = (i: number) => `idx:${i}:${artifacts[i].artifact_id}`
  const selectedValue = artifacts.some((_, i) => tabValue(i) === activeTab)
    ? activeTab
    : tabValue(0)

  if (artifacts.length === 0) {
    return (
      <Card className="flex h-full flex-col">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <FileText className="h-4 w-4" aria-hidden />
            Artifacts
          </CardTitle>
        </CardHeader>
        <CardContent className="flex-1">
          <div
            className="flex flex-col items-center gap-2 py-8 text-center text-sm text-muted-foreground"
            role="status"
          >
            <FileText className="h-5 w-5" aria-hidden />
            No artifacts yet. Generate one after an investigation step.
          </div>
        </CardContent>
      </Card>
    )
  }

  const expanded = expandedId
    ? artifacts.find((a) => a.artifact_id === expandedId)
    : undefined

  const renderArtifact = (a: Artifact) => (
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <Badge variant="outline">{a.scope}</Badge>
        <Badge variant="secondary">{a.artifact_type}</Badge>
        <Badge variant="outline" className="font-mono">{a.format}</Badge>
        {a.created_at && (
          <span className="text-muted-foreground">
            {new Date(a.created_at).toLocaleString()}
          </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Sources: {a.source_tool_calls.length} tool call
        {a.source_tool_calls.length === 1 ? '' : 's'}
      </p>
      {a.content ? (
        // flex-1 min-h-0: the scroll region fills the remaining card height
        // (no fixed height — the bottom is always reachable and the region
        // is never truncated above the card border). horizontal: long
        // Markdown table rows / unbreakable tokens scroll instead of
        // wrapping or stretching the layout.
        <ScrollArea className="min-h-0 flex-1 rounded-md border bg-muted/30 p-3" horizontal>
          <ArtifactMarkdown content={a.content} />
        </ScrollArea>
      ) : a.storage_ref ? (
        <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
          Content stored externally: <span className="font-mono">{a.storage_ref}</span>
        </p>
      ) : (
        <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
          Artifact content is not available.
        </p>
      )}
    </div>
  )

  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <FileText className="h-4 w-4" aria-hidden />
          Artifacts
          <Badge variant="secondary">{artifacts.length}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {artifacts.length === 1 ? (
          renderArtifact(artifacts[0])
        ) : (
          <Tabs
            value={selectedValue}
            onValueChange={(v) => {
              setActiveTab(v)
              // keep the active tab in view after selection (the strip may
              // be scrolled when a far tab was reached via scroll arrows)
              requestAnimationFrame(() => requestAnimationFrame(() => {
                const el = document.querySelector(
                  `[role="tab"][data-state="active"]`,
                ) as HTMLElement | null
                el?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
              }))
            }}
            className="flex min-h-0 flex-1 flex-col"
          >
            {/*
              Tab header: SINGLE horizontal row (never wraps). When tabs
              exceed the available width the row scrolls horizontally inside
              its own bounded strip — independent of the content scroll.
              min-w-0 keeps the strip from forcing the card wider.
            */}
            <div className="min-w-0 overflow-x-auto">
              <TabsList className="inline-flex w-max shrink-0">
                {artifacts.map((_, i) => (
                  <TabsTrigger key={tabValue(i)} value={tabValue(i)}>
                    #{i + 1}
                  </TabsTrigger>
                ))}
              </TabsList>
            </div>
            {artifacts.map((a, i) => (
              <TabsContent
                key={tabValue(i)}
                value={tabValue(i)}
                className="mt-2 min-h-0 flex-1 overflow-hidden"
              >
                {renderArtifact(a)}
              </TabsContent>
            ))}
          </Tabs>
        )}
        {expanded && (
          <Button
            variant="ghost"
            size="sm"
            className="mt-2"
            onClick={onCollapse}
            aria-label="Collapse expanded artifact"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
            Collapse
          </Button>
        )}
        <p className="mt-2 flex items-center gap-1 text-[10px] text-muted-foreground">
          <Maximize2 className="h-3 w-3" aria-hidden />
          Artifacts are composed by the Agent from executed tool results.
        </p>
      </CardContent>
    </Card>
  )
}

/**
 * Readable rendering of the deterministic artifact Markdown. Sections map to
 * the Agent's documented artifact structure (Case, Findings, Timeline,
 * Signal Explanation, Policy References, Investigation Evidence, Evidence
 * Gaps, Source Tool Calls); everything else renders as preformatted text.
 * Content comes verbatim from the backend artifact — nothing is parsed for
 * meaning, and citation markers [n] are preserved exactly.
 */
const SECTION_HEADING_RE = /^(#{1,3}) (.+)$/

function ArtifactMarkdown({ content }: { content: string }) {
  const blocks = content.split('\n')
  return (
    <div className="w-max min-w-full space-y-1 font-sans text-sm leading-relaxed">
      {blocks.map((line, i) => {
        const heading = SECTION_HEADING_RE.exec(line)
        if (heading) {
          const level = heading[1].length
          const text = heading[2]
          if (level === 1) {
            return (
              <p key={i} className="mb-2 text-base font-semibold">{text}</p>
            )
          }
          return (
            <p
              key={i}
              className={
                level === 2
                  ? 'mt-4 mb-1 border-b pb-1 text-sm font-semibold'
                  : 'mt-3 mb-1 text-sm font-medium text-muted-foreground'
              }
            >
              {text}
            </p>
          )
        }
        if (line.trim() === '') return null
        return (
          <p key={i} className="whitespace-pre-wrap">{line}</p>
        )
      })}
    </div>
  )
}
