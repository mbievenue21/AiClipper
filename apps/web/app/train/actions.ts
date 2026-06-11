"use server";

import { mkdir, writeFile } from "fs/promises";
import path from "path";
import { revalidatePath } from "next/cache";

import { db, schema } from "@/lib/db/client";
import { getMediaRoot } from "@/lib/media";
import type { TrainingEditorNotes } from "@/lib/db/schema";
import { defaultProjectName, detectSourceType } from "@/lib/source-url";
import { enqueueJob } from "@/lib/worker";
import { createDatasetAction } from "@/app/profiles/actions";

export async function submitTrainingFeedbackAction(input: {
  profileId: string;
  datasetId?: string;
  datasetName?: string;
  referenceClips?: Array<{
    fileName: string;
    base64: string;
    title?: string;
    vote: "positive" | "negative";
    editorNotes?: TrainingEditorNotes;
  }>;
  referenceUrls?: Array<{
    url: string;
    title?: string;
    vote: "positive" | "negative";
    editorNotes?: TrainingEditorNotes;
  }>;
  candidateFeedback?: Array<{
    startSeconds: number;
    endSeconds: number;
    vote: "accepted" | "rejected";
    projectId?: string;
  }>;
}) {
  let datasetId = input.datasetId;
  if (!datasetId) {
    const created = await createDatasetAction({
      profileId: input.profileId,
      name: input.datasetName ?? `Training session ${new Date().toLocaleDateString()}`,
      description: "Created from home training UI",
    });
    datasetId = created.datasetId;
  }

  const mediaRoot = getMediaRoot();
  const uploadDir = path.join(
    mediaRoot,
    "profiles",
    input.profileId,
    datasetId,
    "uploads",
  );
  await mkdir(uploadDir, { recursive: true });

  const uploads = input.referenceClips ?? [];
  const urls = input.referenceUrls ?? [];
  const clipCount = uploads.length + urls.length;

  if (clipCount === 0) {
    return {
      ok: false as const,
      datasetId: datasetId ?? "",
      message: "Add at least one reference clip (upload or URL).",
    };
  }

  for (const clip of uploads) {
    const buffer = Buffer.from(clip.base64, "base64");
    const safeName = clip.fileName.replace(/[^a-zA-Z0-9._-]/g, "_");
    const dest = path.join(uploadDir, safeName);
    await writeFile(dest, buffer);

    const relPath = path
      .relative(mediaRoot, dest)
      .split(path.sep)
      .join("/");

    await enqueueJob({
      type: "reference_clip_import",
      payload: {
        profile_id: input.profileId,
        dataset_id: datasetId,
        source_path: relPath,
        source_type: "uploaded_clip",
        title: clip.title ?? safeName,
        label: clip.vote === "positive" ? "positive" : "negative",
        editor_notes: clip.editorNotes,
      },
    });
  }

  for (const item of urls) {
    const url = item.url.trim();
    let sourceType: ReturnType<typeof detectSourceType>;
    try {
      sourceType = detectSourceType(url);
    } catch (err) {
      return {
        ok: false as const,
        datasetId: datasetId ?? "",
        message: err instanceof Error ? err.message : "Invalid reference URL.",
      };
    }

    await enqueueJob({
      type: "reference_clip_import",
      payload: {
        profile_id: input.profileId,
        dataset_id: datasetId,
        source_url: url,
        source_type: sourceType,
        title: item.title?.trim() || defaultProjectName(url),
        label: item.vote === "positive" ? "positive" : "negative",
        editor_notes: item.editorNotes,
      },
    });
  }

  if (input.candidateFeedback?.length) {
    for (const fb of input.candidateFeedback) {
      await db.insert(schema.trainingExamples).values({
        datasetId: datasetId!,
        projectId: fb.projectId,
        startSeconds: fb.startSeconds,
        endSeconds: fb.endSeconds,
        label: fb.vote === "accepted" ? "accepted" : "rejected",
        reason: fb.vote === "accepted" ? "user_accept" : "user_reject",
        confidence: 1,
      });
    }
  }

  await enqueueJob({
    type: "profile_train",
    payload: {
      profile_id: input.profileId,
      dataset_id: datasetId,
      n_trials: 30,
      wait_for_imports: true,
      expected_import_count: clipCount,
    },
  });

  revalidatePath("/train");
  revalidatePath(`/profiles/${input.profileId}`);
  revalidatePath("/analytics");

  return {
    ok: true as const,
    datasetId,
    message:
      urls.length > 0 && uploads.length > 0
        ? `Training submitted — downloading ${urls.length} URL(s) and importing ${uploads.length} upload(s).`
        : urls.length > 0
          ? `Training submitted — worker will download ${urls.length} reference clip(s) with yt-dlp.`
          : "Training submitted — worker will optimize the profile config",
  };
}
