"use server";

import { revalidatePath } from "next/cache";
import { and, eq } from "drizzle-orm";
import { z } from "zod";

import { db, schema } from "@/lib/db/client";
import {
  DEFAULT_CAPTION_SETTINGS,
  type CaptionFont,
  type CaptionStyle,
  type ClipCaptionSettings,
  type GeneratedUploadMetadata,
} from "@/lib/db/schema";
import { enqueueJob } from "@/lib/worker";
import { generateMetadata, MetadataGenerationError } from "@/lib/ai/metadata";
import {
  getTrendingTags,
  rankTrendingByRelevance,
  type TrendingTag,
} from "@/lib/youtube/trending";
import { hasYouTubeAccount, YouTubeClientError } from "@/lib/youtube/client";

const captionStyleSchema = z.object({
  font: z
    .enum(["inter", "bebas", "anton", "marker", "mono", "montserrat"])
    .optional(),
  style: z
    .enum(["highlight", "popup", "karaoke", "minimal"])
    .optional(),
  autoColor: z.boolean().optional(),
  primaryColor: z
    .string()
    .regex(/^#?[0-9a-fA-F]{6}$/)
    .optional(),
  accentColor: z
    .string()
    .regex(/^#?[0-9a-fA-F]{6}$/)
    .optional(),
  uppercase: z.boolean().optional(),
});

function normalizeColor(c?: string): string | undefined {
  if (!c) return undefined;
  return c.startsWith("#") ? c : `#${c}`;
}

function mergeStyle(input: unknown): ClipCaptionSettings {
  const parsed = captionStyleSchema.safeParse(input ?? {});
  const partial = parsed.success ? parsed.data : {};
  return {
    font: (partial.font as CaptionFont) ?? DEFAULT_CAPTION_SETTINGS.font,
    style: (partial.style as CaptionStyle) ?? DEFAULT_CAPTION_SETTINGS.style,
    autoColor: partial.autoColor ?? DEFAULT_CAPTION_SETTINGS.autoColor,
    primaryColor:
      normalizeColor(partial.primaryColor) ?? DEFAULT_CAPTION_SETTINGS.primaryColor,
    accentColor:
      normalizeColor(partial.accentColor) ?? DEFAULT_CAPTION_SETTINGS.accentColor,
    uppercase: partial.uppercase ?? DEFAULT_CAPTION_SETTINGS.uppercase,
  };
}

/**
 * Enqueue a render job for a highlight. If the user already rendered this
 * highlight we re-use the existing clip row so the gallery stays stable.
 */
export async function renderHighlightAction(formData: FormData) {
  const projectId = String(formData.get("projectId") || "");
  const highlightId = String(formData.get("highlightId") || "");
  const autoCaption = formData.get("autoCaption") === "on";

  if (!projectId || !highlightId) {
    return { ok: false as const, message: "Missing project or highlight id." };
  }

  // Look for an existing clip row for this highlight (we render one per highlight).
  const existing = db
    .select()
    .from(schema.clips)
    .where(eq(schema.clips.highlightId, highlightId))
    .limit(1)
    .all()[0];

  const styleInput = mergeStyle({
    style: formData.get("captionStyle") || undefined,
    font: formData.get("captionFont") || undefined,
    autoColor: formData.get("captionAutoColor") === "on",
    uppercase: formData.get("captionUppercase") === "on",
    primaryColor: formData.get("captionPrimary") || undefined,
    accentColor: formData.get("captionAccent") || undefined,
  });

  try {
    await enqueueJob({
      type: "render",
      project_id: projectId,
      payload: {
        highlight_id: highlightId,
        clip_id: existing?.id ?? null,
        auto_caption: autoCaption,
        caption_style: styleInput,
      },
    });
  } catch (err) {
    return {
      ok: false as const,
      message: err instanceof Error ? err.message : String(err),
    };
  }

  revalidatePath(`/projects/${projectId}`);
  return { ok: true as const, message: "Render job queued." };
}

/**
 * Apply (or re-apply) captions to an already-rendered clip with the chosen style.
 */
export async function captionClipAction(formData: FormData) {
  const projectId = String(formData.get("projectId") || "");
  const clipId = String(formData.get("clipId") || "");

  if (!projectId || !clipId) {
    return { ok: false as const, message: "Missing project or clip id." };
  }

  const style = mergeStyle({
    style: formData.get("captionStyle") || undefined,
    font: formData.get("captionFont") || undefined,
    autoColor: formData.get("captionAutoColor") === "on",
    uppercase: formData.get("captionUppercase") === "on",
    primaryColor: formData.get("captionPrimary") || undefined,
    accentColor: formData.get("captionAccent") || undefined,
  });

  try {
    await enqueueJob({
      type: "caption",
      project_id: projectId,
      payload: { clip_id: clipId, caption_style: style },
    });
  } catch (err) {
    return {
      ok: false as const,
      message: err instanceof Error ? err.message : String(err),
    };
  }

  revalidatePath(`/projects/${projectId}`);
  return { ok: true as const, message: "Caption job queued." };
}

/**
 * Delete a clip row (cascades through scheduled_uploads via FK).
 * Does NOT remove files on disk — those live under data/videos/<project>/clips
 * and can be cleaned up manually or by a future GC job.
 */
export async function deleteClipAction(formData: FormData) {
  const projectId = String(formData.get("projectId") || "");
  const clipId = String(formData.get("clipId") || "");
  if (!projectId || !clipId) return { ok: false as const, message: "Missing ids." };

  db.delete(schema.clips).where(eq(schema.clips.id, clipId)).run();
  revalidatePath(`/projects/${projectId}`);
  return { ok: true as const, message: "Clip removed." };
}

// -------------------------------------------------------------------------
// Scheduled uploads (Step 12 + 13)
// -------------------------------------------------------------------------

const scheduleSchema = z.object({
  clipId: z.string().min(1),
  projectId: z.string().min(1),
  accountId: z.string().min(1),
  platform: z.enum(["youtube", "instagram"]),
  title: z.string().min(1).max(100),
  description: z.string().max(5000).optional(),
  tags: z.string().max(500).optional(),
  visibility: z.enum(["private", "unlisted", "public"]).default("private"),
  scheduledAtMs: z
    .number()
    .int()
    .min(0)
    .refine((v) => v <= Date.now() + 365 * 24 * 60 * 60 * 1000, {
      message: "Can't schedule more than a year out.",
    }),
  timezone: z.string().min(1).default("America/Chicago"),
});

export type ScheduleUploadInput = z.infer<typeof scheduleSchema>;

/**
 * Create a scheduled upload row. If the schedule time is "now" (within the
 * next 5 seconds), we ALSO enqueue a publish job immediately so the user
 * doesn't have to wait for the scheduler tick.
 */
export async function scheduleUploadAction(
  input: ScheduleUploadInput,
): Promise<{ ok: boolean; message: string; id?: string }> {
  const parsed = scheduleSchema.safeParse(input);
  if (!parsed.success) {
    return {
      ok: false,
      message: parsed.error.issues
        .map((i) => `${i.path.join(".")}: ${i.message}`)
        .join("; "),
    };
  }
  const data = parsed.data;

  const clip = db
    .select()
    .from(schema.clips)
    .where(eq(schema.clips.id, data.clipId))
    .limit(1)
    .all()[0];
  if (!clip) return { ok: false, message: "Clip not found." };
  if (clip.status !== "ready") {
    return { ok: false, message: "Clip is not ready to publish yet." };
  }

  const account = db
    .select()
    .from(schema.accounts)
    .where(
      and(
        eq(schema.accounts.id, data.accountId),
        eq(schema.accounts.platform, data.platform),
      ),
    )
    .limit(1)
    .all()[0];
  if (!account) {
    return {
      ok: false,
      message: `No ${data.platform} account connected. Add one in /accounts.`,
    };
  }

  const tagsArray = (data.tags || "")
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);

  const inserted = db
    .insert(schema.scheduledUploads)
    .values({
      clipId: data.clipId,
      accountId: data.accountId,
      platform: data.platform,
      title: data.title,
      description: data.description || null,
      tagsJson: tagsArray.length > 0 ? tagsArray : null,
      visibility: data.visibility,
      timezone: data.timezone,
      scheduledFor: new Date(data.scheduledAtMs),
      status: "pending",
    })
    .returning({ id: schema.scheduledUploads.id })
    .all()[0];

  // If they hit "post now" or "soon", kick off the publish job immediately.
  if (data.scheduledAtMs <= Date.now() + 5_000) {
    try {
      await enqueueJob({
        type: "publish",
        project_id: data.projectId,
        payload: { upload_id: inserted.id },
      });
    } catch (err) {
      return {
        ok: false,
        message: `Saved schedule but worker rejected the publish job: ${
          err instanceof Error ? err.message : String(err)
        }`,
      };
    }
  }

  revalidatePath(`/projects/${data.projectId}`);
  return {
    ok: true,
    id: inserted.id,
    message:
      data.scheduledAtMs <= Date.now() + 5_000
        ? "Uploading now."
        : "Scheduled. The worker will publish at the chosen time.",
  };
}

export async function cancelScheduledUploadAction(formData: FormData) {
  const uploadId = String(formData.get("uploadId") || "");
  const projectId = String(formData.get("projectId") || "");
  if (!uploadId) return { ok: false as const, message: "Missing upload id." };
  db.update(schema.scheduledUploads)
    .set({ status: "cancelled" })
    .where(eq(schema.scheduledUploads.id, uploadId))
    .run();
  if (projectId) revalidatePath(`/projects/${projectId}`);
  return { ok: true as const, message: "Upload cancelled." };
}

// -------------------------------------------------------------------------
// AI-generated upload metadata (title / description / tags / hashtags)
// -------------------------------------------------------------------------

export type GenerateMetadataResult =
  | { ok: true; message: string; metadata: GeneratedUploadMetadata }
  | { ok: false; message: string };

/**
 * Run Gemini Flash against the clip's transcript + project context to
 * produce upload-ready title, description, tags, and hashtags. Result is
 * persisted on the highlight row so it survives a page refresh and can
 * be edited freely by the user before publishing.
 *
 * Called from the Schedule dialog's "Generate with AI" button.
 */
export async function generateMetadataAction(input: {
  highlightId: string;
  projectId: string;
}): Promise<GenerateMetadataResult> {
  if (!input.highlightId) {
    return { ok: false, message: "Missing highlight id." };
  }
  try {
    const meta = await generateMetadata({ highlightId: input.highlightId });
    if (input.projectId) revalidatePath(`/projects/${input.projectId}`);
    return {
      ok: true,
      message: "Generated AI metadata.",
      metadata: meta,
    };
  } catch (err) {
    const msg =
      err instanceof MetadataGenerationError
        ? err.message
        : err instanceof Error
          ? err.message
          : String(err);
    return { ok: false, message: msg };
  }
}

// -------------------------------------------------------------------------
// Trending tag suggestions via YouTube Data API.
// -------------------------------------------------------------------------

export type TrendingTagsResult =
  | {
      ok: true;
      relevant: TrendingTag[];
      general: TrendingTag[];
      cachedFor: string;
    }
  | { ok: false; message: string };

/**
 * Pulls "what's trending right now" tags from YouTube via the connected
 * account's OAuth token, then ranks them against the clip's existing words
 * so the UI can highlight tags that fit the content vs. generic trending.
 *
 * Region is currently hard-coded to US; a future setting can let the user
 * pick a region per project.
 */
export async function getTrendingTagSuggestionsAction(input: {
  seedWords: string[];
  regionCode?: string;
}): Promise<TrendingTagsResult> {
  if (!hasYouTubeAccount()) {
    return {
      ok: false,
      message: "Connect a YouTube account on /accounts to see trending tags.",
    };
  }
  try {
    const trending = await getTrendingTags(input.regionCode || "US");
    const { relevant, general } = rankTrendingByRelevance(
      trending,
      input.seedWords ?? [],
    );
    return {
      ok: true,
      relevant: relevant.slice(0, 12),
      general: general.slice(0, 18),
      cachedFor: (input.regionCode || "US").toUpperCase(),
    };
  } catch (err) {
    const msg =
      err instanceof YouTubeClientError
        ? err.message
        : err instanceof Error
          ? err.message
          : String(err);
    return { ok: false, message: msg };
  }
}
