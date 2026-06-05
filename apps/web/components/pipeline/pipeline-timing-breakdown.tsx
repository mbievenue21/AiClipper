"use client";

import { AlertTriangle, Clock, Timer } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  formatDurationMs,
  type ProjectTimingBreakdown,
  type StageTimingRow,
} from "@/lib/pipeline/analytics-types";

function statusVariant(status: StageTimingRow["status"]) {
  switch (status) {
    case "ok":
      return "default" as const;
    case "timeout":
      return "destructive" as const;
    case "failed":
      return "destructive" as const;
    case "partial":
      return "secondary" as const;
    case "skipped":
      return "outline" as const;
    default:
      return "secondary" as const;
  }
}

export function PipelineTimingBreakdownView({
  data,
  compact = false,
}: {
  data: ProjectTimingBreakdown;
  compact?: boolean;
}) {
  const maxMs = Math.max(...data.stages.map((s) => s.durationMs), 1);

  if (!data.stages.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No timing data yet. Run a full pipeline (or re-analyze) to record stage
        durations.
      </p>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="inline-flex items-center gap-1.5 font-medium">
          <Timer className="size-4 text-muted-foreground" />
          Total: {formatDurationMs(data.totalMs)}
        </span>
        {data.bottleneck && (
          <Badge variant="secondary">
            Bottleneck:{" "}
            {data.stages.find((s) => s.key === data.bottleneck)?.label ??
              data.bottleneck}
          </Badge>
        )}
        {data.videoDurationSeconds != null && data.videoDurationSeconds > 0 && (
          <span className="text-muted-foreground">
            Video: {formatDurationMs(data.videoDurationSeconds * 1000)}
          </span>
        )}
        {data.fromBackfill && (
          <Badge variant="outline">Estimated from jobs</Badge>
        )}
        {data.isReanalysis && <Badge variant="outline">Re-analysis run</Badge>}
      </div>

      <div className={cn("space-y-2", compact && "space-y-1.5")}>
        {data.stages.map((stage) => (
          <StageBar key={stage.key} stage={stage} maxMs={maxMs} compact={compact} />
        ))}
      </div>

      {data.stages.some((s) => s.status === "timeout" || s.status === "failed") && (
        <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>
            One or more stages failed or timed out. Check worker logs and the
            issues listed in the README (Pegasus timeout, Marengo hits).
          </span>
        </div>
      )}
    </div>
  );
}

function StageBar({
  stage,
  maxMs,
  compact,
}: {
  stage: StageTimingRow;
  maxMs: number;
  compact?: boolean;
}) {
  const pct = Math.max(2, Math.round((stage.durationMs / maxMs) * 100));
  const isBottleneck = stage.durationMs === maxMs && stage.durationMs > 0;

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span
          className={cn(
            "font-medium",
            isBottleneck && "text-primary",
          )}
        >
          {stage.label}
        </span>
        <div className="flex items-center gap-2">
          <Badge variant={statusVariant(stage.status)} className="text-[10px]">
            {stage.status}
          </Badge>
          <span className="tabular-nums text-muted-foreground">
            {formatDurationMs(stage.durationMs)}
          </span>
        </div>
      </div>
      <div
        className={cn(
          "overflow-hidden rounded-full bg-muted",
          compact ? "h-1.5" : "h-2",
        )}
      >
        <div
          className={cn(
            "h-full rounded-full transition-all",
            stage.status === "timeout" || stage.status === "failed"
              ? "bg-destructive/80"
              : isBottleneck
                ? "bg-primary"
                : "bg-primary/50",
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function PipelineTimingSummaryChip({
  data,
}: {
  data: ProjectTimingBreakdown;
}) {
  if (!data.stages.length) return null;
  const bottleneck = data.stages.find((s) => s.key === data.bottleneck);
  return (
    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
      <Clock className="size-3" />
      {formatDurationMs(data.totalMs)}
      {bottleneck ? ` · slowest: ${bottleneck.label}` : null}
    </span>
  );
}
