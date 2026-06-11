"use server";

import { eq } from "drizzle-orm";
import { revalidatePath } from "next/cache";
import { z } from "zod";

import { db, schema } from "@/lib/db/client";
import { enqueueJob } from "@/lib/worker";

const createProfileSchema = z.object({
  name: z.string().min(2).max(120),
  slug: z
    .string()
    .min(2)
    .max(80)
    .regex(/^[a-z0-9_]+$/),
  description: z.string().max(500).optional(),
  game: z.string().max(60).optional(),
  contentType: z.string().max(60).optional(),
});

export async function createProfileAction(
  input: z.infer<typeof createProfileSchema>,
) {
  const parsed = createProfileSchema.safeParse(input);
  if (!parsed.success) {
    return { ok: false as const, message: parsed.error.issues[0]?.message ?? "Invalid input" };
  }

  const existing = await db
    .select({ id: schema.highlightProfiles.id })
    .from(schema.highlightProfiles)
    .where(eq(schema.highlightProfiles.slug, parsed.data.slug))
    .limit(1);
  if (existing[0]) {
    return { ok: false as const, message: "Slug already in use" };
  }

  const [profile] = await db
    .insert(schema.highlightProfiles)
    .values({
      name: parsed.data.name,
      slug: parsed.data.slug,
      description: parsed.data.description,
      game: parsed.data.game,
      contentType: parsed.data.contentType,
      status: "draft",
    })
    .returning();

  const defaultConfig = await db
    .select()
    .from(schema.highlightProfileVersions)
    .where(eq(schema.highlightProfileVersions.profileId, "valorant_reaction"))
    .limit(1);

  const configJson = defaultConfig[0]?.configJson ?? {
    metadata: { name: parsed.data.name, slug: parsed.data.slug },
    candidateSources: {
      audioPeaks: true,
      transcriptKeywords: true,
      semanticPhrases: true,
      chatBursts: true,
      sceneCuts: true,
      ocrEvents: false,
    },
    timing: {
      minDurationSeconds: 20,
      targetDurationSeconds: 45,
      maxDurationSeconds: 60,
      preRollSeconds: 8,
      postRollSeconds: 2,
      mergeWindowSeconds: 12,
      dedupeOverlapThreshold: 0.55,
    },
    keywords: {},
    phrases: [],
    scoreWeights: {
      audioPeak: 0.28,
      keyword: 0.22,
      semanticPhrase: 0.18,
      chatBurst: 0.15,
      scene: 0.08,
      ocr: 0.05,
    },
    thresholds: {
      audioPeakMin: 0.55,
      chatBurstMin: 0.5,
      embeddingSimilarityMin: 0.62,
      sceneCutBonus: 0.15,
    },
    penalties: {
      duplicate: 0.25,
      tooShort: 0.3,
      tooLong: 0.2,
      weakTranscript: 0.15,
    },
    normalization: { audioZScoreCap: 3, chatZScoreCap: 3 },
  };

  const [version] = await db
    .insert(schema.highlightProfileVersions)
    .values({
      profileId: profile.id,
      versionNumber: 1,
      configJson,
      modelType: "config_only",
      isActive: true,
    })
    .returning();

  await db
    .update(schema.highlightProfiles)
    .set({ activeVersionId: version.id, status: "active" })
    .where(eq(schema.highlightProfiles.id, profile.id));

  revalidatePath("/profiles");
  return { ok: true as const, profileId: profile.id, message: "Profile created" };
}

export async function createDatasetAction(input: {
  profileId: string;
  name: string;
  description?: string;
}) {
  const [dataset] = await db
    .insert(schema.trainingDatasets)
    .values({
      profileId: input.profileId,
      name: input.name,
      description: input.description,
    })
    .returning();

  revalidatePath(`/profiles/${input.profileId}`);
  return { ok: true as const, datasetId: dataset.id };
}

export async function startProfileTrainingAction(input: {
  profileId: string;
  datasetId: string;
  nTrials?: number;
}) {
  try {
    await enqueueJob({
      type: "profile_train",
      payload: {
        profile_id: input.profileId,
        dataset_id: input.datasetId,
        n_trials: input.nTrials ?? 40,
      },
    });
    revalidatePath(`/profiles/${input.profileId}`);
    return { ok: true as const, message: "Training job queued" };
  } catch (err) {
    return {
      ok: false as const,
      message: err instanceof Error ? err.message : "Failed to queue training",
    };
  }
}

export async function retrainFromFeedbackAction(input: {
  profileId: string;
  datasetId: string;
  projectId?: string;
}) {
  try {
    await enqueueJob({
      type: "profile_retrain_from_feedback",
      project_id: input.projectId,
      payload: {
        profile_id: input.profileId,
        dataset_id: input.datasetId,
        project_id: input.projectId,
      },
    });
    revalidatePath(`/profiles/${input.profileId}`);
    return { ok: true as const, message: "Retrain job queued" };
  } catch (err) {
    return {
      ok: false as const,
      message: err instanceof Error ? err.message : "Failed to queue retrain",
    };
  }
}

export async function setActiveProfileVersionAction(input: {
  profileId: string;
  versionId: string;
}) {
  await db
    .update(schema.highlightProfileVersions)
    .set({ isActive: false })
    .where(eq(schema.highlightProfileVersions.profileId, input.profileId));

  await db
    .update(schema.highlightProfileVersions)
    .set({ isActive: true })
    .where(eq(schema.highlightProfileVersions.id, input.versionId));

  await db
    .update(schema.highlightProfiles)
    .set({ activeVersionId: input.versionId })
    .where(eq(schema.highlightProfiles.id, input.profileId));

  revalidatePath(`/profiles/${input.profileId}`);
  return { ok: true as const, message: "Active version updated" };
}
