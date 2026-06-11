import { and, desc, eq } from "drizzle-orm";

import { db, schema } from "@/lib/db/client";

export async function listHighlightProfiles() {
  return db
    .select()
    .from(schema.highlightProfiles)
    .orderBy(desc(schema.highlightProfiles.updatedAt));
}

export async function getHighlightProfile(idOrSlug: string) {
  const byId = await db
    .select()
    .from(schema.highlightProfiles)
    .where(eq(schema.highlightProfiles.id, idOrSlug))
    .limit(1);
  if (byId[0]) return byId[0];

  const bySlug = await db
    .select()
    .from(schema.highlightProfiles)
    .where(eq(schema.highlightProfiles.slug, idOrSlug))
    .limit(1);
  return bySlug[0] ?? null;
}

export async function getProfileVersions(profileId: string) {
  return db
    .select()
    .from(schema.highlightProfileVersions)
    .where(eq(schema.highlightProfileVersions.profileId, profileId))
    .orderBy(desc(schema.highlightProfileVersions.versionNumber));
}

export async function getActiveProfileVersion(profileId: string) {
  const profile = await getHighlightProfile(profileId);
  if (!profile?.activeVersionId) {
    const versions = await getProfileVersions(profileId);
    return versions.find((v) => v.isActive) ?? versions[0] ?? null;
  }
  const rows = await db
    .select()
    .from(schema.highlightProfileVersions)
    .where(eq(schema.highlightProfileVersions.id, profile.activeVersionId))
    .limit(1);
  return rows[0] ?? null;
}

export async function listTrainingDatasets(profileId: string) {
  return db
    .select()
    .from(schema.trainingDatasets)
    .where(eq(schema.trainingDatasets.profileId, profileId))
    .orderBy(desc(schema.trainingDatasets.updatedAt));
}

export async function getTrainingRuns(profileId: string) {
  return db
    .select()
    .from(schema.profileTrainingRuns)
    .where(eq(schema.profileTrainingRuns.profileId, profileId))
    .orderBy(desc(schema.profileTrainingRuns.createdAt))
    .limit(20);
}

export async function getReferenceClips(datasetId: string) {
  return db
    .select()
    .from(schema.referenceClips)
    .where(eq(schema.referenceClips.datasetId, datasetId))
    .orderBy(desc(schema.referenceClips.createdAt));
}

export async function getProfileAnalyticsOverview() {
  const profiles = await listHighlightProfiles();
  const runs = await db
    .select()
    .from(schema.profileTrainingRuns)
    .orderBy(desc(schema.profileTrainingRuns.createdAt))
    .limit(50);

  const examples = await db
    .select()
    .from(schema.trainingExamples)
    .orderBy(desc(schema.trainingExamples.createdAt))
    .limit(200);

  return { profiles, runs, examples };
}

export async function getProjectProfileCandidates(projectId: string) {
  return db
    .select()
    .from(schema.profileScoredCandidates)
    .where(eq(schema.profileScoredCandidates.projectId, projectId))
    .orderBy(desc(schema.profileScoredCandidates.score));
}

export async function getProfileFeedbackExamples(profileId: string, limit = 30) {
  const datasets = await db
    .select()
    .from(schema.trainingDatasets)
    .where(
      and(
        eq(schema.trainingDatasets.profileId, profileId),
        eq(schema.trainingDatasets.name, "Editor feedback"),
      ),
    )
    .limit(1);

  const dataset = datasets[0];
  if (!dataset) return [];

  return db
    .select()
    .from(schema.trainingExamples)
    .where(eq(schema.trainingExamples.datasetId, dataset.id))
    .orderBy(desc(schema.trainingExamples.createdAt))
    .limit(limit);
}
