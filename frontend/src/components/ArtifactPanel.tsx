import { Artifact, ArtifactType, ProvenanceType } from '@/types/task'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { CheckCircle2, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'

const PROVENANCE_LABELS: Record<ProvenanceType, string> = {
  [ProvenanceType.DIRECT_EVIDENCE]: 'Evidence-backed',
  [ProvenanceType.RULE_DERIVED]: 'Rule-derived',
  [ProvenanceType.ML_DERIVED]: 'ML-derived',
  [ProvenanceType.GRAPH_DERIVED]: 'Graph-derived',
  [ProvenanceType.PRIMARY_REASON]: 'Primary reason',
  [ProvenanceType.UNKNOWN]: 'Unknown source',
}

const PROVENANCE_VARIANTS: Record<ProvenanceType, 'default' | 'secondary' | 'outline' | 'destructive'> = {
  [ProvenanceType.DIRECT_EVIDENCE]: 'default',
  [ProvenanceType.RULE_DERIVED]: 'secondary',
  [ProvenanceType.ML_DERIVED]: 'outline',
  [ProvenanceType.GRAPH_DERIVED]: 'outline',
  [ProvenanceType.PRIMARY_REASON]: 'default',
  [ProvenanceType.UNKNOWN]: 'destructive',
}

interface ArtifactPanelProps {
  artifacts: Artifact[]
  className?: string
}

export function ArtifactPanel({ artifacts, className }: ArtifactPanelProps) {
  const getArtifactByType = (type: ArtifactType) =>
    artifacts.find((a) => a.type === type)

  const findings = getArtifactByType('findings')
  const actions = getArtifactByType('actions')
  const citations = getArtifactByType('citations')
  const narrative = getArtifactByType('narrative')

  const hasAnyArtifact =
    findings || actions || citations || narrative

  if (!hasAnyArtifact) {
    return (
      <Card className={cn(className)}>
        <CardHeader>
          <CardTitle className="text-lg">Artifacts</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            No artifacts available yet
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className={cn(className)}>
      <CardHeader>
        <CardTitle className="text-lg">Results</CardTitle>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="findings">
          <TabsList className="grid grid-cols-4 w-full">
            <TabsTrigger value="findings" disabled={!findings}>
              Findings
            </TabsTrigger>
            <TabsTrigger value="actions" disabled={!actions}>
              Actions
            </TabsTrigger>
            <TabsTrigger value="citations" disabled={!citations}>
              Citations
            </TabsTrigger>
            <TabsTrigger value="narrative" disabled={!narrative}>
              Summary
            </TabsTrigger>
          </TabsList>

          <TabsContent value="findings" className="mt-4">
            <ScrollArea className="h-[400px]">
              {findings && Array.isArray(findings.data) && findings.data.length > 0 ? (
                <div className="space-y-3 pr-4">
                  {/* Helper text explaining the data source */}
                  <div className="text-xs text-muted-foreground pb-2 border-b mb-3">
                    <strong>Source Findings</strong>: Authoritative findings returned by the Risk Platform evidence API
                  </div>
                  <div className="space-y-3">
                  {(findings.data as Array<{ finding_id?: string; claim?: string; provenance?: { type: ProvenanceType; source: string } }>).map(
                    (finding, index) => {
                      const provenance = finding.provenance
                      const provenanceType = provenance?.type as ProvenanceType || ProvenanceType.UNKNOWN
                      const source = provenance?.source || 'unknown'

                      return (
                        <div
                          key={finding.finding_id || index}
                          className="p-3 border rounded-lg bg-card"
                        >
                          <div className="flex items-start gap-2 mb-2">
                            <Badge variant="outline" className="text-xs">
                              F-{String(index + 1).padStart(3, '0')}
                            </Badge>
                            <Badge variant={PROVENANCE_VARIANTS[provenanceType]} className="text-xs">
                              {PROVENANCE_LABELS[provenanceType]}
                            </Badge>
                            {source && (
                              <Badge variant="outline" className="text-xs text-muted-foreground">
                                {source}
                              </Badge>
                            )}
                          </div>
                          <div className="ml-1">
                            {finding.claim && (
                              <p className="text-sm font-medium">{finding.claim}</p>
                            )}
                          </div>
                        </div>
                      )
                    }
                  )}
                  </div>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground p-4">
                  No findings available
                </p>
              )}
            </ScrollArea>
          </TabsContent>

          <TabsContent value="actions" className="mt-4">
            <ScrollArea className="h-[400px]">
              {actions && Array.isArray(actions.data) && actions.data.length > 0 ? (
                <div className="space-y-2 pr-4">
                  {(actions.data as string[]).map((action, index) => (
                    <div
                      key={index}
                      className="flex items-start gap-3 p-3 border rounded-lg bg-card hover:bg-accent/50 transition-colors"
                    >
                      <div className="flex items-center gap-2 mt-0.5">
                        <div className="h-4 w-4 rounded border border-input flex items-center justify-center bg-background" />
                      </div>
                      <span className="text-sm">{action}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground p-4">
                  No actions available
                </p>
              )}
            </ScrollArea>
          </TabsContent>

          <TabsContent value="citations" className="mt-4">
            <ScrollArea className="h-[400px]">
              {citations && citations.data && typeof citations.data === 'object' ? (
                <div className="space-y-4 pr-4">
                  {/* Citation Summary */}
                  <div className="grid grid-cols-2 gap-3">
                    <div className="min-w-0 p-3 border rounded-lg bg-card text-center">
                      <div className="text-2xl font-bold text-green-600">
                        {String((citations.data as any).supported_count || 0)}
                      </div>
                      <div className="text-xs text-muted-foreground break-words">
                        Policy Supported
                      </div>
                    </div>
                    <div className="min-w-0 p-3 border rounded-lg bg-card text-center">
                      <div className="text-2xl font-bold text-blue-600">
                        {String((citations.data as any).source_grounded_count || 0)}
                      </div>
                      <div className="text-xs text-muted-foreground break-words">
                        Source Grounded
                      </div>
                    </div>
                    <div className="min-w-0 p-3 border rounded-lg bg-card text-center">
                      <div className="text-2xl font-bold text-red-600">
                        {String((citations.data as any).unsupported_count || 0)}
                      </div>
                      <div className="text-xs text-muted-foreground break-words">
                        Unsupported
                      </div>
                    </div>
                    <div className="min-w-0 p-3 border rounded-lg bg-card text-center">
                      <div className="text-2xl font-bold">
                        {Math.round(((citations.data as any).citation_accuracy || 0) * 100)}%
                      </div>
                      <div className="text-xs text-muted-foreground break-words">
                        Policy Citation Coverage
                      </div>
                    </div>
                  </div>

                  <Separator />

                  {/* Metric Explanation */}
                  <div className="text-xs text-muted-foreground p-2 bg-muted/50 rounded border border-border/50">
                    <strong>Policy Citation Coverage</strong>: Percentage of investigation claims supported by retrieved policy documents.
                  </div>

                  {/* Unsupported Claims */}
                  {(citations.data as any).unsupported_claims &&
                  Array.isArray((citations.data as any).unsupported_claims) &&
                  (citations.data as any).unsupported_claims.length > 0 ? (
                    <div className="space-y-2">
                      <h4 className="text-sm font-medium flex items-center gap-2">
                        <AlertTriangle className="h-4 w-4 text-amber-500" />
                        Unsupported Claims
                      </h4>
                      {(citations.data as any).unsupported_claims.map(
                        (item: any, index: number) => (
                          <div
                            key={index}
                            className="p-3 border rounded-lg bg-amber-50 border-amber-200"
                          >
                            <div className="text-xs text-amber-800">
                              <span className="font-medium">Reason:</span>{' '}
                              {item.reason || 'No reason provided'}
                            </div>
                            {item.claim && (
                              <div className="text-xs text-amber-700 mt-1">
                                <span className="font-medium">Claim:</span>{' '}
                                {typeof item.claim === 'string'
                                  ? item.claim
                                  : JSON.stringify(item.claim)}
                              </div>
                            )}
                          </div>
                        )
                      )}
                    </div>
                  ) : (
                    <div className="flex items-center gap-2 text-sm text-green-600 bg-green-50 p-3 rounded-lg border border-green-200">
                      <CheckCircle2 className="h-4 w-4" />
                      <span>All claims are supported</span>
                    </div>
                  )}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground p-4">
                  No citation data available
                </p>
              )}
            </ScrollArea>
          </TabsContent>

          <TabsContent value="narrative" className="mt-4">
            <ScrollArea className="h-[400px]">
              {narrative && narrative.data ? (
                <div className="space-y-4 pr-4">
                  {typeof narrative.data === 'object' && narrative.data !== null &&
                    'risk_level' in narrative.data ? (
                      <div className="space-y-4">
                        {/* Case ID */}
                        <div className="flex items-center justify-between p-3 border rounded-lg bg-card">
                          <span className="text-sm text-muted-foreground">Case ID</span>
                          <span className="text-sm font-medium">{narrative.data.case_id || 'N/A'}</span>
                        </div>

                        {/* Risk Level */}
                        <div className="flex items-center justify-between p-3 border rounded-lg bg-card">
                          <span className="text-sm text-muted-foreground">Risk Level</span>
                          <Badge variant={
                            narrative.data.risk_level === 'CRITICAL' ? 'destructive' :
                            narrative.data.risk_level === 'HIGH' ? 'destructive' :
                            narrative.data.risk_level === 'MEDIUM' ? 'secondary' : 'outline'
                          } className="text-sm">
                            {narrative.data.risk_level || 'Unknown'}
                          </Badge>
                        </div>

                        {/* Risk Score */}
                        <div className="flex items-center justify-between p-3 border rounded-lg bg-card">
                          <span className="text-sm text-muted-foreground">Risk Score</span>
                          <span className="text-lg font-bold">
                            {narrative.data.risk_score !== undefined ? Number(narrative.data.risk_score).toFixed(2) : 'N/A'}
                          </span>
                        </div>

                        {/* Primary Reason */}
                        <div className="p-3 border rounded-lg bg-card">
                          <div className="text-sm text-muted-foreground mb-1">Primary Reason</div>
                          <div className="text-sm font-medium">{narrative.data.primary_reason || 'Not specified'}</div>
                        </div>

                        {/* Recommended Action */}
                        <div className="p-3 border rounded-lg bg-card">
                          <div className="text-sm text-muted-foreground mb-1">Recommended Action</div>
                          <div className="text-sm font-medium">{narrative.data.recommended_action || 'Not specified'}</div>
                        </div>

                        {/* Detection Signals */}
                        {narrative.data.detection_signals && Object.keys(narrative.data.detection_signals).length > 0 && (
                          <div className="p-3 border rounded-lg bg-card">
                            <div className="text-sm text-muted-foreground mb-2">Detection Signals</div>
                            <div className="grid grid-cols-3 gap-2">
                              {narrative.data.detection_signals.ml_score !== undefined && (
                                <div className="text-center p-2 bg-muted/50 rounded">
                                  <div className="text-xs text-muted-foreground">ML</div>
                                  <div className="text-sm font-bold">{Number(narrative.data.detection_signals.ml_score).toFixed(2)}</div>
                                </div>
                              )}
                              {narrative.data.detection_signals.rule_score !== undefined && (
                                <div className="text-center p-2 bg-muted/50 rounded">
                                  <div className="text-xs text-muted-foreground">Rule</div>
                                  <div className="text-sm font-bold">{Number(narrative.data.detection_signals.rule_score).toFixed(2)}</div>
                                </div>
                              )}
                              {narrative.data.detection_signals.graph_score !== undefined && (
                                <div className="text-center p-2 bg-muted/50 rounded">
                                  <div className="text-xs text-muted-foreground">Graph</div>
                                  <div className="text-sm font-bold">{Number(narrative.data.detection_signals.graph_score).toFixed(2)}</div>
                                </div>
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="rounded border bg-card">
                        <div
                          style={{
                            maxHeight: '400px',
                            overflowX: 'auto',
                            overflowY: 'auto',
                            WebkitOverflowScrolling: 'touch',
                          }}
                        >
                          <pre
                            style={{
                              fontSize: '12px',
                              padding: '8px',
                              whiteSpace: 'pre',
                              margin: '0',
                              width: 'max-content',
                              maxWidth: 'none',
                            }}
                          >
                            {JSON.stringify(narrative.data, null, 2)}
                          </pre>
                        </div>
                      </div>
                  )}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground p-4">
                  No summary available
                </p>
              )}
            </ScrollArea>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}
