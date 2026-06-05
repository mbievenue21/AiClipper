"use server";

import { eq } from "drizzle-orm";
import { redirect } from "next/navigation";

import { db, schema } from "@/lib/db/client";
import { DEFAULT_PROJECT_SETTINGS, type ProjectSettings } from "@/lib/db/schema";
import { defaultProjectName, detectSourceType } from "@/lib/source-url";
import { enqueueIngestJob } from "@/lib/worker";

export type CreateProjectState = {
  error?: string;
};

function parseSettings(formData: FormData): ProjectSettings {
  const raw = {
    topN: Number(formData.get("topN") ?? DEFAULT_PROJECT_SETTINGS.topN),
    minClipSeconds: Number(
      formData.get("minClipSeconds") ?? DEFAULT_PROJECT_SETTINGS.minClipSeconds,
    ),
    maxClipSeconds: Number(
      formData.get("maxClipSeconds") ?? DEFAULT_PROJECT_SETTINGS.maxClipSeconds,
    ),
    aspect: String(formData.get("aspect") ?? DEFAULT_PROJECT_SETTINGS.aspect),
    vibe: String(formData.get("vibe") ?? "").trim(),
    preRollSeconds: Number(
      formData.get("preRollSeconds") ?? DEFAULT_PROJECT_SETTINGS.preRollSeconds,
    ),
    tailPaddingSeconds: Number(
      formData.get("tailPaddingSeconds") ??
        DEFAULT_PROJECT_SETTINGS.tailPaddingSeconds,
    ),
    analyzeModel: String(
      formData.get("analyzeModel") ?? DEFAULT_PROJECT_SETTINGS.analyzeModel,
    ),
  };

  const topN = Math.max(1, Math.min(20, Math.floor(raw.topN || 3)));
  let minClip = Math.max(5, Math.min(180, Math.floor(raw.minClipSeconds || 20)));
  let maxClip = Math.max(10, Math.min(180, Math.floor(raw.maxClipSeconds || 60)));
  if (maxClip < minClip) [minClip, maxClip] = [maxClip, minClip];

  const aspect: ProjectSettings["aspect"] = (
    ["9:16", "16:9", "1:1"] as const
  ).includes(raw.aspect as ProjectSettings["aspect"])
    ? (raw.aspect as ProjectSettings["aspect"])
    : DEFAULT_PROJECT_SETTINGS.aspect;

  // Pre-roll capped at 20s; tail at 10s. Defaults match the schema.
  const preRollSeconds = Math.max(
    0,
    Math.min(20, Math.floor(raw.preRollSeconds || 0)),
  );
  const tailPaddingSeconds = Math.max(
    0,
    Math.min(10, Math.floor(raw.tailPaddingSeconds || 0)),
  );

  const analyzeModel: ProjectSettings["analyzeModel"] = (
    ["gemini-2.5-pro", "gemini-2.5-flash"] as const
  ).includes(raw.analyzeModel as ProjectSettings["analyzeModel"])
    ? (raw.analyzeModel as ProjectSettings["analyzeModel"])
    : DEFAULT_PROJECT_SETTINGS.analyzeModel;

  return {
    topN,
    minClipSeconds: minClip,
    maxClipSeconds: maxClip,
    aspect,
    vibe: raw.vibe.slice(0, 200),
    preRollSeconds,
    tailPaddingSeconds,
    analyzeModel,
  };
}

export async function createProject(
  _prev: CreateProjectState,
  formData: FormData,
): Promise<CreateProjectState> {
  const sourceUrl = String(formData.get("sourceUrl") ?? "").trim();
  const nameInput = String(formData.get("name") ?? "").trim();

  if (!sourceUrl) {
    return { error: "Paste a YouTube or Twitch VOD URL." };
  }

  let sourceType: ReturnType<typeof detectSourceType>;
  try {
    sourceType = detectSourceType(sourceUrl);
  } catch (err) {
    return { error: err instanceof Error ? err.message : "Invalid URL." };
  }

  const name = nameInput || defaultProjectName(sourceUrl);
  const settings = parseSettings(formData);

  const [project] = await db
    .insert(schema.projects)
    .values({
      name,
      sourceUrl,
      sourceType,
      status: "pending",
      settingsJson: settings,
    })
    .returning();

  try {
    await enqueueIngestJob(project.id, sourceUrl);
  } catch (err) {
    await db
      .update(schema.projects)
      .set({
        status: "failed",
        notes:
          err instanceof Error
            ? err.message.slice(0, 2000)
            : "Failed to enqueue ingest job",
      })
      .where(eq(schema.projects.id, project.id));

    return {
      error:
        err instanceof Error
          ? err.message
          : "Could not reach the worker. Run `pnpm dev` and try again.",
    };
  }

  redirect(`/projects/${project.id}`);
}
