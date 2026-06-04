"use server";

import { eq } from "drizzle-orm";
import { redirect } from "next/navigation";

import { db, schema } from "@/lib/db/client";
import { defaultProjectName, detectSourceType } from "@/lib/source-url";
import { enqueueIngestJob } from "@/lib/worker";

export type CreateProjectState = {
  error?: string;
};

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

  const [project] = await db
    .insert(schema.projects)
    .values({
      name,
      sourceUrl,
      sourceType,
      status: "pending",
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
