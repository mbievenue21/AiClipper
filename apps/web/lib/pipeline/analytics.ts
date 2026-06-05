import "server-only";

import { and, desc, eq, inArray, sql } from "drizzle-orm";

import { db, schema } from "@/lib/db/client";
import {
  PIPELINE_STAGE_DEFS,
  type PipelineStageKey,
  type PipelineStageStatus,
  type PipelineStageTiming,
} from "@/lib/db/schema";

import type {
  AnalyticsProjectRow,
  ProjectTimingBreakdown,
  StageTimingRow,
} from "./analytics-types";

export type {
  AnalyticsProjectRow,
  ProjectTimingBreakdown,
  StageTimingRow,
} from "./analytics-types";
export { formatDurationMs } from "./analytics-types";

const JOB_TO_STAGE: Record<string, PipelineStageKey> = {
  ingest: "ingest",
  transcribe: "transcribe",
  twelvelabs_index: "twelvelabs_index",
  twelvelabs_analyze: "twelvelabs_visual",
};

function stageDef(key: PipelineStageKey) {
  return PIPELINE_STAGE_DEFS.find((s) => s.key === key)!;
}

function toMs(d: Date | number | null | undefined): number | null {
  if (d == null) return null;
  return d instanceof Date ? d.getTime() : Number(d);
}

function buildStageRows(
  timings: PipelineStageTiming[],
): StageTimingRow[] {
  const byKey = new Map(timings.map((t) => [t.stage, t]));
  return PIPELINE_STAGE_DEFS.map((def) => {
    const row = byKey.get(def.key);
    return {
      key: def.key,
      label: def.label,
      group: def.group,
      durationMs: row?.durationMs ?? 0,
      startedAt: toMs(row?.startedAt),
      finishedAt: toMs(row?.finishedAt),
      status: (row?.status as PipelineStageStatus) ?? "skipped",
      meta: (row?.metaJson as Record<string, unknown>) ?? null,
      jobId: row?.jobId ?? null,
    };
  }).filter((s) => s.durationMs > 0 || s.status !== "skipped");
}

function pickBottleneck(stages: StageTimingRow[]): PipelineStageKey | null {
  const measured = stages.filter((s) => s.durationMs > 0 && s.status !== "skipped");
  if (!measured.length) return null;
  return measured.reduce((a, b) => (b.durationMs > a.durationMs ? b : a)).key;
}

/** Backfill job-level timings from the jobs table when no analytics rows exist. */
function backfillFromJobs(projectId: string): StageTimingRow[] {
  const jobs = db
    .select()
    .from(schema.jobs)
    .where(
      and(
        eq(schema.jobs.projectId, projectId),
        eq(schema.jobs.status, "succeeded"),
        inArray(schema.jobs.type, [
          "ingest",
          "transcribe",
          "twelvelabs_index",
          "twelvelabs_analyze",
          "analyze",
        ]),
      ),
    )
    .orderBy(desc(schema.jobs.finishedAt))
    .all();

  const latestByType = new Map<string, (typeof jobs)[0]>();
  for (const j of jobs) {
    if (!latestByType.has(j.type)) latestByType.set(j.type, j);
  }

  const rows: StageTimingRow[] = [];
  for (const [jobType, job] of latestByType) {
    const stageKey = JOB_TO_STAGE[jobType];
    if (!stageKey) continue;
    const started = toMs(job.startedAt);
    const finished = toMs(job.finishedAt);
    if (!started || !finished) continue;
    const def = stageDef(stageKey);
    const err = job.errorMessage ?? "";
    let status: PipelineStageStatus = "ok";
    if (/timed out|timeout/i.test(err)) status = "timeout";
    rows.push({
      key: stageKey,
      label: def.label,
      group: def.group,
      durationMs: Math.max(0, finished - started),
      startedAt: started,
      finishedAt: finished,
      status,
      meta: { backfill: true, jobType: job.type },
      jobId: job.id,
    });
  }

  const analyzeJob = latestByType.get("analyze");
  const result = analyzeJob?.resultJson as Record<string, unknown> | null;
  const sub = result?.stage_timings_ms as Record<string, number> | undefined;
  if (sub) {
    for (const [key, ms] of Object.entries(sub)) {
      const def = PIPELINE_STAGE_DEFS.find((d) => d.key === key);
      if (!def) continue;
      const k = key as PipelineStageKey;
      rows.push({
        key: k,
        label: def.label,
        group: def.group,
        durationMs: ms,
        startedAt: null,
        finishedAt: null,
        status: "ok",
        meta: { backfill: true, source: "analyze_result" },
        jobId: analyzeJob?.id ?? null,
      });
    }
  }

  return rows.sort(
    (a, b) =>
      PIPELINE_STAGE_DEFS.findIndex((d) => d.key === a.key) -
      PIPELINE_STAGE_DEFS.findIndex((d) => d.key === b.key),
  );
}

export function getLatestRunForProject(projectId: string) {
  return db
    .select()
    .from(schema.pipelineRuns)
    .where(eq(schema.pipelineRuns.projectId, projectId))
    .orderBy(desc(schema.pipelineRuns.startedAt))
    .limit(1)
    .all()[0] ?? null;
}

export function getProjectTimingBreakdown(
  projectId: string,
): ProjectTimingBreakdown | null {
  const project = db
    .select()
    .from(schema.projects)
    .where(eq(schema.projects.id, projectId))
    .limit(1)
    .all()[0];
  if (!project) return null;

  const run = getLatestRunForProject(projectId);
  let stages: StageTimingRow[] = [];
  let fromBackfill = false;

  if (run) {
    const timings = db
      .select()
      .from(schema.pipelineStageTimings)
      .where(eq(schema.pipelineStageTimings.runId, run.id))
      .all();
    stages = buildStageRows(timings);
  }

  if (!stages.length && project.status === "ready") {
    stages = backfillFromJobs(projectId);
    fromBackfill = stages.length > 0;
  }

  const totalMs = stages.reduce((s, r) => s + r.durationMs, 0);

  const video = db
    .select({ duration: schema.videos.durationSeconds })
    .from(schema.videos)
    .where(eq(schema.videos.projectId, projectId))
    .limit(1)
    .all()[0];

  return {
    runId: run?.id ?? null,
    projectId,
    projectName: project.name,
    projectStatus: project.status,
    videoDurationSeconds:
      run?.videoDurationSeconds ?? video?.duration ?? null,
    twelvelabsEnabled: run?.twelvelabsEnabled ?? false,
    isReanalysis: run?.isReanalysis ?? false,
    runStartedAt: toMs(run?.startedAt),
    runFinishedAt: toMs(run?.finishedAt),
    totalMs,
    bottleneck: pickBottleneck(stages),
    stages,
    fromBackfill,
  };
}

export function getAnalyticsOverview(): AnalyticsProjectRow[] {
  const projects = db
    .select()
    .from(schema.projects)
    .where(eq(schema.projects.status, "ready"))
    .orderBy(desc(schema.projects.updatedAt))
    .limit(100)
    .all();

  const rows: AnalyticsProjectRow[] = [];
  for (const p of projects) {
    const highlightCount =
      db
        .select({ count: sql<number>`count(*)` })
        .from(schema.highlights)
        .innerJoin(schema.videos, eq(schema.highlights.videoId, schema.videos.id))
        .where(eq(schema.videos.projectId, p.id))
        .all()[0]?.count ?? 0;

    if (Number(highlightCount) === 0) continue;

    const breakdown = getProjectTimingBreakdown(p.id);
    if (!breakdown || breakdown.stages.length === 0) continue;

    rows.push({
      ...breakdown,
      highlightCount: Number(highlightCount),
    });
  }

  return rows.sort((a, b) => b.totalMs - a.totalMs);
}

/** Aggregate median duration per stage across projects (for comparison headers). */
export function stageMedians(rows: AnalyticsProjectRow[]): Partial<Record<PipelineStageKey, number>> {
  const buckets = new Map<PipelineStageKey, number[]>();
  for (const row of rows) {
    for (const s of row.stages) {
      if (s.durationMs <= 0) continue;
      const arr = buckets.get(s.key) ?? [];
      arr.push(s.durationMs);
      buckets.set(s.key, arr);
    }
  }
  const out: Partial<Record<PipelineStageKey, number>> = {};
  for (const [key, vals] of buckets) {
    vals.sort((a, b) => a - b);
    const mid = Math.floor(vals.length / 2);
    out[key] =
      vals.length % 2 === 0
        ? Math.round((vals[mid - 1]! + vals[mid]!) / 2)
        : vals[mid]!;
  }
  return out;
}
