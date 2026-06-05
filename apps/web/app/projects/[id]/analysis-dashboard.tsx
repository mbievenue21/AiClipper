import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import { Activity, Cpu, ExternalLink, ScrollText, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { TwelveLabsConfigStatus } from "@/lib/twelvelabs/status";

export type AnalysisJobLog = {
  id: string;
  type: string;
  status: string;
  progress: number;
  progressMessage: string | null;
  errorMessage: string | null;
  resultJson: Record<string, unknown> | null;
  createdAt: Date | number;
  finishedAt: Date | number | null;
};

export type VisualSegmentSummary = {
  segmentType: string;
  count: number;
};

const JOB_LABELS: Record<string, string> = {
  twelvelabs_index: "TwelveLabs — upload & index",
  twelvelabs_analyze: "TwelveLabs — Pegasus + Marengo",
  analyze: "Local fusion + Gemini rerank",
};

const STATUS_VARIANT: Record<
  string,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "outline",
  running: "secondary",
  succeeded: "default",
  failed: "destructive",
  cancelled: "outline",
};

function ts(value: Date | number | null | undefined): number | null {
  if (value == null) return null;
  return value instanceof Date ? value.getTime() : Number(value);
}

function formatResultSummary(result: Record<string, unknown> | null): string | null {
  if (!result) return null;
  const parts: string[] = [];
  if (typeof result.skipped === "boolean" && result.skipped) {
    parts.push(`skipped: ${String(result.reason ?? "unknown")}`);
  }
  if (result.chunk_count != null) {
    parts.push(`upload chunks: ${result.chunk_count}`);
  }
  if (result.upload_chunk_count != null) {
    parts.push(`analyzed chunks: ${result.upload_chunk_count}`);
  }
  if (result.visual_segment_count != null) {
    parts.push(`visual segments: ${result.visual_segment_count}`);
  }
  if (result.highlight_count != null) {
    parts.push(`highlights: ${result.highlight_count}`);
  }
  if (result.candidate_count != null) {
    parts.push(`candidates: ${result.candidate_count}`);
  }
  if (result.used_llm != null) {
    parts.push(`gemini: ${result.used_llm ? "yes" : "no"}`);
  }
  if (result.analyze_model) {
    parts.push(`model: ${String(result.analyze_model)}`);
  }
  if (result.twelvelabs_used != null) {
    parts.push(`twelvelabs fused: ${result.twelvelabs_used ? "yes" : "no"}`);
  }
  if (result.provider_video_id) {
    parts.push(`video id: ${String(result.provider_video_id).slice(0, 12)}…`);
  }
  if (result.failed_open) {
    parts.push("failed open — local pipeline continued");
  }
  if (result.error) {
    parts.push(`error: ${String(result.error).slice(0, 80)}`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function ProjectAnalysisDashboard({
  config,
  analyzeModelTier,
  indexStatus,
  indexError,
  providerVideoId,
  indexChunkCount,
  indexReadyCount,
  visualSegmentCount,
  visualByType,
  visualCandidateCount,
  pegasusCandidateCount,
  marengoCandidateCount,
  recentJobs,
  projectNotes,
  show,
}: {
  config: TwelveLabsConfigStatus;
  analyzeModelTier: "pro" | "flash";
  indexStatus: string | null;
  indexError: string | null;
  providerVideoId: string | null;
  indexChunkCount: number;
  indexReadyCount: number;
  visualSegmentCount: number;
  visualByType: VisualSegmentSummary[];
  visualCandidateCount: number;
  pegasusCandidateCount: number;
  marengoCandidateCount: number;
  recentJobs: AnalysisJobLog[];
  projectNotes: string | null;
  show: boolean;
}) {
  if (!show) return null;

  const configReady =
    config.enabled && config.apiKeyConfigured && config.indexIdConfigured;
  const geminiRerankModel =
    analyzeModelTier === "pro" ? config.models.geminiPro : config.models.geminiFlash;

  return (
    <Card className="mb-4 border-dashed">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Activity className="size-4" />
          Video analysis dashboard
        </CardTitle>
        <CardDescription>
          Live job messages and TwelveLabs visual evidence for this project.
          Worker terminal logs show full structlog output during runs.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {/* Config strip */}
        <div className="flex flex-wrap gap-1.5">
          <Badge variant={config.enabled ? "default" : "secondary"}>
            TwelveLabs {config.enabled ? "enabled" : "disabled"}
          </Badge>
          {config.enabled && (
            <>
              <Badge variant={config.apiKeyConfigured ? "outline" : "destructive"}>
                API key {config.apiKeyConfigured ? "set" : "missing"}
              </Badge>
              <Badge variant={config.indexIdConfigured ? "outline" : "destructive"}>
                Index ID {config.indexIdConfigured ? "set" : "missing"}
              </Badge>
              <Badge variant="outline">
                fail-open {config.failOpen ? "on" : "off"}
              </Badge>
            </>
          )}
          {config.multimodalEnabled && (
            <Badge variant="outline">Gemini multimodal refine</Badge>
          )}
        </div>

        {/* Models in use — the cutting-edge stack driving this analysis */}
        <div className="rounded-md border bg-muted/20 p-3">
          <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <Cpu className="size-3.5" />
            Models in use
          </p>
          <div className="flex flex-wrap gap-1.5">
            <Badge variant="outline" className="gap-1">
              <Sparkles className="size-3 text-violet-500" />
              Gemini rerank · <span className="font-mono">{geminiRerankModel}</span>
            </Badge>
            {config.enabled && (
              <>
                <Badge variant="outline">
                  Search · <span className="font-mono">{config.models.marengo}</span>
                </Badge>
                <Badge variant="outline">
                  Segmentation · <span className="font-mono">{config.models.pegasus}</span>
                </Badge>
              </>
            )}
            {config.multimodalEnabled && (
              <Badge variant="outline">
                Multimodal ·{" "}
                <span className="font-mono">{config.models.geminiMultimodal}</span>
              </Badge>
            )}
            <Badge variant="secondary">
              thinking: {config.models.geminiThinkingLevel}
            </Badge>
          </div>
          <p className="mt-2 text-[11px] text-muted-foreground">
            Gemini tier for this project:{" "}
            <span className="font-medium text-foreground">
              {analyzeModelTier === "pro" ? "Pro" : "Flash"}
            </span>
            . Change it via the Re-analyze dialog.
          </p>
        </div>

        {!config.enabled && (
          <p className="text-xs text-muted-foreground">
            Set <code className="rounded bg-muted px-1">TWELVELABS_ENABLED=true</code> in{" "}
            <code className="rounded bg-muted px-1">.env</code> to use video-native analysis.
            Local transcript/audio/chat analysis still runs.
          </p>
        )}

        {config.enabled && !configReady && (
          <p className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-xs text-amber-800 dark:text-amber-200">
            TwelveLabs is enabled but not fully configured. Index/analyze jobs will skip
            and the local pipeline will continue (fail-open).
          </p>
        )}

        {/* Index + visual stats */}
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-md border bg-muted/30 p-3 text-xs">
            <p className="font-medium text-foreground">TwelveLabs index</p>
            <dl className="mt-2 space-y-1 text-muted-foreground">
              <div className="flex justify-between gap-2">
                <dt>Status</dt>
                <dd className="font-mono text-foreground">{indexStatus ?? "—"}</dd>
              </div>
              {indexChunkCount > 0 && (
                <div className="flex justify-between gap-2">
                  <dt>Upload chunks</dt>
                  <dd className="font-mono text-foreground">
                    {indexReadyCount}/{indexChunkCount} ready
                  </dd>
                </div>
              )}
              {providerVideoId && indexChunkCount <= 1 && (
                <div className="flex justify-between gap-2">
                  <dt>Provider video</dt>
                  <dd className="truncate font-mono text-foreground" title={providerVideoId}>
                    {providerVideoId.slice(0, 16)}…
                  </dd>
                </div>
              )}
              {indexChunkCount > 1 && (
                <div className="text-muted-foreground">
                  Large VOD split into {indexChunkCount} TwelveLabs uploads; timestamps
                  merged back to full video time.
                </div>
              )}
              {indexError && (
                <div className="text-destructive">{indexError.slice(0, 200)}</div>
              )}
            </dl>
          </div>

          <div className="rounded-md border bg-muted/30 p-3 text-xs">
            <p className="font-medium text-foreground">Visual evidence</p>
            <dl className="mt-2 space-y-1 text-muted-foreground">
              <div className="flex justify-between gap-2">
                <dt>Visual segments</dt>
                <dd className="font-mono text-foreground">{visualSegmentCount}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt>Visual candidates</dt>
                <dd className="font-mono text-foreground">{visualCandidateCount}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt>Pegasus / Marengo</dt>
                <dd className="font-mono text-foreground">
                  {pegasusCandidateCount} / {marengoCandidateCount}
                </dd>
              </div>
            </dl>
          </div>
        </div>

        {visualByType.length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">
              Segment types detected
            </p>
            <div className="flex flex-wrap gap-1.5">
              {visualByType.map((row) => (
                <Badge key={row.segmentType} variant="secondary">
                  {row.segmentType.replace(/_/g, " ")} ({row.count})
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Job log timeline */}
        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <p className="flex items-center gap-1.5 text-xs font-medium">
              <ScrollText className="size-3.5" />
              Analysis job log
            </p>
            <Link
              href="/admin"
              className="inline-flex items-center gap-1 text-[11px] text-muted-foreground underline hover:text-foreground"
            >
              All jobs
              <ExternalLink className="size-3" />
            </Link>
          </div>

          {recentJobs.length === 0 ? (
            <p className="text-xs text-muted-foreground">
              No analysis jobs yet. Run ingest → transcribe, or use Re-analyze.
            </p>
          ) : (
            <ul className="space-y-2">
              {recentJobs.map((job) => {
                const summary = formatResultSummary(job.resultJson);
                const created = ts(job.createdAt);
                const finished = ts(job.finishedAt);
                const isActive =
                  job.status === "pending" || job.status === "running";
                return (
                  <li
                    key={job.id}
                    className="rounded-md border p-2.5 text-xs"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-medium">
                        {JOB_LABELS[job.type] ?? job.type}
                      </span>
                      <Badge variant={STATUS_VARIANT[job.status] ?? "secondary"}>
                        {job.status}
                      </Badge>
                    </div>
                    {isActive && (
                      <Progress
                        value={Math.round(job.progress * 100)}
                        className="mt-2 h-1"
                      />
                    )}
                    {job.progressMessage && (
                      <p className="mt-1.5 text-muted-foreground">
                        {job.progressMessage}
                      </p>
                    )}
                    {summary && (
                      <p className="mt-1 text-foreground/80">{summary}</p>
                    )}
                    {job.errorMessage && (
                      <p className="mt-1 text-destructive">{job.errorMessage}</p>
                    )}
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      {created
                        ? formatDistanceToNow(created, { addSuffix: true })
                        : "—"}
                      {finished && job.status === "succeeded"
                        ? " · finished"
                        : null}
                      <span className="ml-2 font-mono opacity-60">{job.id}</span>
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {projectNotes && (
          <div className="rounded-md bg-muted/40 p-2 text-xs text-muted-foreground whitespace-pre-wrap">
            <span className="font-medium text-foreground">Pipeline notes: </span>
            {projectNotes}
          </div>
        )}

        <p className="text-[11px] text-muted-foreground">
          Tip: watch the worker terminal while jobs run — look for{" "}
          <code className="rounded bg-muted px-1">twelvelabs_*</code> and{" "}
          <code className="rounded bg-muted px-1">analyze_done</code> log lines.
        </p>
      </CardContent>
    </Card>
  );
}
