import { and, eq } from "drizzle-orm";

import { db, schema } from "@/lib/db/client";
import type {
  HighlightReason,
  TrainingEditorNotes,
  TrainingExampleLabel,
  TrainingExampleReason,
} from "@/lib/db/schema";
import {
  hasEditorNotesContent,
  mergeEditorNotesIntoFeatures,
} from "@/lib/profiles/editor-notes";
import { enqueueJob } from "@/lib/worker";

import { getHighlightProfile } from "./queries";

function asFeaturesJson(
  value: unknown,
): Record<string, unknown> | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return undefined;
  }
  return value as Record<string, unknown>;
}

async function enqueueEditorEnrichment(
  profileId: string,
  exampleId: string | null,
  editorNotes?: TrainingEditorNotes,
) {
  if (!exampleId || !editorNotes || !hasEditorNotesContent(editorNotes)) {
    return;
  }
  if (editorNotes.enrichWithGemini === false) {
    return;
  }
  try {
    await enqueueJob({
      type: "enrich_training_feedback",
      payload: {
        profile_id: profileId,
        training_example_id: exampleId,
      },
    });
  } catch {
    // Worker offline — notes still stored; enrich can run before next train.
  }
}

export async function getOrCreateFeedbackDataset(profileId: string) {
  const existing = await db
    .select()
    .from(schema.trainingDatasets)
    .where(
      and(
        eq(schema.trainingDatasets.profileId, profileId),
        eq(schema.trainingDatasets.name, "Editor feedback"),
      ),
    )
    .limit(1);

  if (existing[0]) return existing[0];

  const [row] = await db
    .insert(schema.trainingDatasets)
    .values({
      profileId,
      name: "Editor feedback",
      description: "Auto-collected from highlight and clip feedback",
      sourceNotes: "feedback_loop",
    })
    .returning();

  return row;
}

async function storeProfileFeedbackExample(input: {
  projectId: string;
  profileId: string;
  startSeconds: number;
  endSeconds: number;
  label: "accepted" | "rejected";
  reason?: TrainingExampleReason | "user_accept" | "user_reject";
  featuresJson?: unknown;
  editorNotes?: TrainingEditorNotes;
}) {
  const dataset = await getOrCreateFeedbackDataset(input.profileId);

  const label: TrainingExampleLabel =
    input.label === "accepted" ? "accepted" : "rejected";
  const reason: TrainingExampleReason =
    input.reason ?? (input.label === "accepted" ? "user_accept" : "user_reject");

  const mergedFeatures = mergeEditorNotesIntoFeatures(
    asFeaturesJson(input.featuresJson),
    input.editorNotes,
  );

  const dup = await db
    .select({
      id: schema.trainingExamples.id,
      featuresJson: schema.trainingExamples.featuresJson,
    })
    .from(schema.trainingExamples)
    .where(
      and(
        eq(schema.trainingExamples.datasetId, dataset.id),
        eq(schema.trainingExamples.projectId, input.projectId),
        eq(schema.trainingExamples.startSeconds, input.startSeconds),
        eq(schema.trainingExamples.endSeconds, input.endSeconds),
        eq(schema.trainingExamples.label, label),
      ),
    )
    .limit(1);

  let exampleId: string | null = null;

  if (dup[0]) {
    exampleId = dup[0].id;
    const combined = mergeEditorNotesIntoFeatures(
      dup[0].featuresJson ?? undefined,
      input.editorNotes,
    );
    if (combined) {
      await db
        .update(schema.trainingExamples)
        .set({ featuresJson: combined })
        .where(eq(schema.trainingExamples.id, dup[0].id));
    }
  } else {
    const [row] = await db
      .insert(schema.trainingExamples)
      .values({
        datasetId: dataset.id,
        projectId: input.projectId,
        startSeconds: input.startSeconds,
        endSeconds: input.endSeconds,
        label,
        reason,
        confidence: 1,
        featuresJson: mergedFeatures,
      })
      .returning({ id: schema.trainingExamples.id });
    exampleId = row?.id ?? null;
  }

  await enqueueEditorEnrichment(
    input.profileId,
    exampleId,
    input.editorNotes,
  );

  return { datasetId: dataset.id, profileId: input.profileId, exampleId };
}

async function resolveProjectProfileId(
  projectId: string,
  profileId?: string | null,
) {
  return (
    profileId ??
    (
      await db
        .select({ settingsJson: schema.projects.settingsJson })
        .from(schema.projects)
        .where(eq(schema.projects.id, projectId))
        .limit(1)
    )[0]?.settingsJson?.highlightProfileId ??
    null
  );
}

export async function recordCandidateFeedback(input: {
  projectId: string;
  profileId?: string | null;
  startSeconds: number;
  endSeconds: number;
  label: "accepted" | "rejected";
  breakdown?: unknown;
  editorNotes?: TrainingEditorNotes;
  autoRetrain?: boolean;
}) {
  const profileId = await resolveProjectProfileId(
    input.projectId,
    input.profileId,
  );
  if (!profileId) {
    return { stored: false as const, reason: "no_profile" };
  }

  const profile = await getHighlightProfile(profileId);
  if (!profile) {
    return { stored: false as const, reason: "profile_not_found" };
  }

  const stored = await storeProfileFeedbackExample({
    projectId: input.projectId,
    profileId: profile.id,
    startSeconds: input.startSeconds,
    endSeconds: input.endSeconds,
    label: input.label,
    featuresJson: input.breakdown ?? undefined,
    editorNotes: input.editorNotes,
  });

  if (input.autoRetrain !== false) {
    try {
      await enqueueJob({
        type: "profile_retrain_from_feedback",
        project_id: input.projectId,
        payload: {
          profile_id: profile.id,
          dataset_id: stored.datasetId,
          project_id: input.projectId,
          skip_clip_feedback_import: true,
          example_only: true,
        },
      });
    } catch {
      // Worker may be offline; feedback is still stored.
    }
  }

  return { stored: true as const, ...stored };
}

export async function recordHighlightFeedback(input: {
  projectId: string;
  highlightId: string;
  profileId?: string | null;
  startSeconds: number;
  endSeconds: number;
  label: "accepted" | "rejected";
  reason?: TrainingExampleReason | "user_accept" | "user_reject";
  reasonSnapshot?: HighlightReason | null;
  editorNotes?: TrainingEditorNotes;
  autoRetrain?: boolean;
}) {
  const profileId = await resolveProjectProfileId(
    input.projectId,
    input.profileId,
  );

  if (!profileId) {
    return { stored: false as const, reason: "no_profile" };
  }

  const profile = await getHighlightProfile(profileId);
  if (!profile) {
    return { stored: false as const, reason: "profile_not_found" };
  }

  const stored = await storeProfileFeedbackExample({
    projectId: input.projectId,
    profileId: profile.id,
    startSeconds: input.startSeconds,
    endSeconds: input.endSeconds,
    label: input.label,
    reason: input.reason,
    featuresJson: input.reasonSnapshot ?? undefined,
    editorNotes: input.editorNotes,
  });

  await db
    .update(schema.highlights)
    .set({ status: input.label === "accepted" ? "approved" : "rejected" })
    .where(eq(schema.highlights.id, input.highlightId));

  if (input.autoRetrain !== false) {
    try {
      await enqueueJob({
        type: "profile_retrain_from_feedback",
        project_id: input.projectId,
        payload: {
          profile_id: profile.id,
          dataset_id: stored.datasetId,
          project_id: input.projectId,
          skip_clip_feedback_import: true,
          example_only: true,
        },
      });
    } catch {
      // Worker may be offline; feedback is still stored.
    }
  }

  return { stored: true as const, ...stored };
}
