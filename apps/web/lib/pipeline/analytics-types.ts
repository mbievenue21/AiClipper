import type { PipelineStageKey, PipelineStageStatus } from "@/lib/db/schema";

/** Client-safe types + formatters for pipeline timing UI. */

export type StageTimingRow = {
  key: PipelineStageKey;
  label: string;
  group: "core" | "twelvelabs" | "analyze";
  durationMs: number;
  startedAt: number | null;
  finishedAt: number | null;
  status: PipelineStageStatus;
  meta: Record<string, unknown> | null;
  jobId: string | null;
};

export type ProjectTimingBreakdown = {
  runId: string | null;
  projectId: string;
  projectName: string;
  projectStatus: string;
  videoDurationSeconds: number | null;
  twelvelabsEnabled: boolean;
  isReanalysis: boolean;
  runStartedAt: number | null;
  runFinishedAt: number | null;
  totalMs: number;
  bottleneck: PipelineStageKey | null;
  stages: StageTimingRow[];
  fromBackfill: boolean;
};

export type AnalyticsProjectRow = ProjectTimingBreakdown & {
  highlightCount: number;
};

export function formatDurationMs(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rem = Math.round(s % 60);
  if (m < 60) return `${m}m ${rem}s`;
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return `${h}h ${rm}m`;
}
