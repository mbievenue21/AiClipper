import { and, desc, eq, inArray, sql } from "drizzle-orm";

import { db, schema } from "@/lib/db/client";

const TRAINING_JOB_TYPES = [
  "reference_clip_import",
  "reference_feature_extract",
  "profile_train",
  "profile_evaluate",
  "profile_retrain_from_feedback",
  "enrich_training_feedback",
] as const;

export async function getProfileTrainingStatus(profileId: string) {
  const recentJobs = await db
    .select({
      id: schema.jobs.id,
      type: schema.jobs.type,
      status: schema.jobs.status,
      progress: schema.jobs.progress,
      progressMessage: schema.jobs.progressMessage,
      payloadJson: schema.jobs.payloadJson,
      createdAt: schema.jobs.createdAt,
      finishedAt: schema.jobs.finishedAt,
    })
    .from(schema.jobs)
    .where(inArray(schema.jobs.type, [...TRAINING_JOB_TYPES]))
    .orderBy(desc(schema.jobs.createdAt))
    .limit(80);

  const profileJobs = recentJobs.filter(
    (j) => j.payloadJson?.profile_id === profileId,
  );
  const activeJobs = profileJobs.filter(
    (j) => j.status === "pending" || j.status === "running",
  );

  const runs = await db
    .select()
    .from(schema.profileTrainingRuns)
    .where(eq(schema.profileTrainingRuns.profileId, profileId))
    .orderBy(desc(schema.profileTrainingRuns.createdAt))
    .limit(5);

  const activeRun =
    runs.find((r) => r.status === "running" || r.status === "queued") ?? null;

  const datasets = await db
    .select({ id: schema.trainingDatasets.id })
    .from(schema.trainingDatasets)
    .where(eq(schema.trainingDatasets.profileId, profileId));

  const datasetIds = datasets.map((d) => d.id);
  let clipCount = 0;
  let exampleCount = 0;
  let positiveExamples = 0;
  let negativeExamples = 0;

  if (datasetIds.length > 0) {
    clipCount = (
      await db
        .select({ count: sql<number>`count(*)` })
        .from(schema.referenceClips)
        .where(inArray(schema.referenceClips.datasetId, datasetIds))
    )[0]?.count ?? 0;

    const examples = await db
      .select({ label: schema.trainingExamples.label })
      .from(schema.trainingExamples)
      .where(inArray(schema.trainingExamples.datasetId, datasetIds));

    exampleCount = examples.length;
    for (const ex of examples) {
      if (
        ex.label === "positive" ||
        ex.label === "accepted" ||
        ex.label === "published"
      ) {
        positiveExamples += 1;
      } else {
        negativeExamples += 1;
      }
    }
  }

  return {
    isTraining: activeJobs.length > 0 || activeRun !== null,
    activeJobs: activeJobs.map((j) => ({
      id: j.id,
      type: j.type,
      status: j.status,
      progress: j.progress,
      progressMessage: j.progressMessage,
    })),
    activeRun: activeRun
      ? {
          id: activeRun.id,
          status: activeRun.status,
          optimizer: activeRun.optimizer,
          metricsJson: activeRun.metricsJson,
        }
      : null,
    clipCount,
    exampleCount,
    positiveExamples,
    negativeExamples,
    latestRun: runs[0] ?? null,
  };
}
