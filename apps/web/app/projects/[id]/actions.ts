"use server";

import { revalidatePath } from "next/cache";
import { and, eq, inArray } from "drizzle-orm";
import { z } from "zod";

import { db, schema } from "@/lib/db/client";
import {
  DEFAULT_CAPTION_SETTINGS,
  ANALYZE_MODEL_LABELS,
  DEFAULT_PROJECT_SETTINGS,
  normalizeAnalyzeModel,
  type CaptionFont,
  type CaptionStyle,
  type CaptionSegmentOverride,
  type ClipCaptionSettings,
  type GeneratedUploadMetadata,
  type ProjectSettings,
} from "@/lib/db/schema";
import { enqueueJob } from "@/lib/worker";
import { deleteProjectPermanently } from "@/lib/projects/delete-project";
import { generateMetadata, MetadataGenerationError } from "@/lib/ai/metadata";
import {
  getTrendingTags,
  rankTrendingByRelevance,
  type TrendingTag,
} from "@/lib/youtube/trending";
import { hasYouTubeAccount, YouTubeClientError } from "@/lib/youtube/client";
import { sanitizeYouTubeTags } from "@/lib/youtube/tags";
import { parseTranscript, TranscriptParseError } from "@/lib/transcripts/parse";
import {
  computeEditorWindow,
  sourceCutFromTrim,
} from "@/lib/clips/editor-window";
import {
  getRankingPreferences,
  recordClipFeedback,
} from "@/lib/ranking/preferences";
import {
  recordCandidateFeedback,
  recordHighlightFeedback,
} from "@/lib/profiles/feedback";
import type { ClipSignalVotes, HighlightReason } from "@/lib/db/schema";

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

const captionSegmentSchema = z.object({
  start: z.number().min(0),
  end: z.number().min(0),
  text: z.string().max(500),
});

const saveClipEditSchema = z.object({
  projectId: z.string().min(1),
  clipId: z.string().min(1),
  trimStart: z.number().min(0),
  trimEnd: z.number().min(0),
  editorWindowStart: z.number().min(0),
  editorWindowEnd: z.number().positive(),
  captionSegments: z.array(captionSegmentSchema),
  replaceOriginal: z.boolean(),
});

/**
 * Save clip editor changes: re-cut from source + re-burn captions.
 */
export async function saveClipEditAction(input: {
  projectId: string;
  clipId: string;
  trimStart: number;
  trimEnd: number;
  editorWindowStart: number;
  editorWindowEnd: number;
  captionSegments: CaptionSegmentOverride[];
  replaceOriginal: boolean;
}) {
  const parsed = saveClipEditSchema.safeParse(input);
  if (!parsed.success) {
    return {
      ok: false as const,
      message: parsed.error.issues.map((i) => i.message).join("; "),
    };
  }

  const clip = db
    .select()
    .from(schema.clips)
    .where(eq(schema.clips.id, parsed.data.clipId))
    .limit(1)
    .all()[0];
  if (!clip) {
    return { ok: false as const, message: "Clip not found." };
  }

  const highlight = db
    .select()
    .from(schema.highlights)
    .where(eq(schema.highlights.id, clip.highlightId))
    .limit(1)
    .all()[0];
  if (!highlight) {
    return { ok: false as const, message: "Highlight not found." };
  }

  const style = (clip.captionStyleJson as ClipCaptionSettings) ?? DEFAULT_CAPTION_SETTINGS;
  const prefs = await getRankingPreferences();
  const { cutStart, cutEnd } = sourceCutFromTrim(
    {
      windowStart: parsed.data.editorWindowStart,
      windowEnd: parsed.data.editorWindowEnd,
      windowDuration: parsed.data.editorWindowEnd - parsed.data.editorWindowStart,
      highlightOffsetStart: 0,
      highlightOffsetEnd: 0,
    },
    parsed.data.trimStart,
    parsed.data.trimEnd,
  );

  try {
    await enqueueJob({
      type: "reedit",
      project_id: parsed.data.projectId,
      payload: {
        parent_clip_id: parsed.data.clipId,
        clip_id: parsed.data.clipId,
        trim_start: parsed.data.trimStart,
        trim_end: parsed.data.trimEnd,
        editor_window_start: parsed.data.editorWindowStart,
        editor_window_end: parsed.data.editorWindowEnd,
        editor_pad_before: prefs.editorPadBeforeSeconds,
        editor_pad_after: prefs.editorPadAfterSeconds,
        caption_segments: parsed.data.captionSegments,
        caption_style: style,
        replace_original: parsed.data.replaceOriginal,
        burn_captions: true,
        version_label: parsed.data.replaceOriginal
          ? null
          : `Edited ${new Date().toLocaleString()}`,
      },
    });

    await recordClipFeedback({
      clipId: parsed.data.clipId,
      highlightId: highlight.id,
      projectId: parsed.data.projectId,
      highlightStart: highlight.startSeconds,
      highlightEnd: highlight.endSeconds,
      sourceStart: cutStart,
      sourceEnd: cutEnd,
      reason: (highlight.reasonJson as HighlightReason) ?? null,
      notes: "editor_trim_save",
      applyLearning: true,
      signalVotes: {},
      overallVote: undefined,
    });
  } catch (err) {
    return {
      ok: false as const,
      message: err instanceof Error ? err.message : String(err),
    };
  }

  revalidatePath(`/projects/${parsed.data.projectId}`);
  return { ok: true as const, message: "Re-edit job queued." };
}

const clipFeedbackSchema = z.object({
  projectId: z.string().min(1),
  clipId: z.string().min(1),
  highlightId: z.string().min(1),
  overallVote: z.enum(["up", "down"]).optional(),
  signalVotes: z
    .record(z.string(), z.enum(["up", "down", "skip"]))
    .optional(),
  highlightStart: z.number(),
  highlightEnd: z.number(),
  sourceStart: z.number().nullable().optional(),
  sourceEnd: z.number().nullable().optional(),
  reason: z.any().optional(),
});

export async function submitProfileCandidateFeedbackAction(input: {
  projectId: string;
  profileId?: string | null;
  startSeconds: number;
  endSeconds: number;
  vote: "accepted" | "rejected";
  breakdown?: Record<string, unknown> | null;
  editorNotes?: import("@/lib/db/schema").TrainingEditorNotes;
}) {
  try {
    const result = await recordCandidateFeedback({
      projectId: input.projectId,
      profileId: input.profileId,
      startSeconds: input.startSeconds,
      endSeconds: input.endSeconds,
      label: input.vote,
      breakdown: input.breakdown ?? undefined,
      editorNotes: input.editorNotes,
      autoRetrain: true,
    });
    revalidatePath(`/projects/${input.projectId}`);
    if (!result.stored) {
      return {
        ok: false as const,
        message: "No highlight profile configured for this project.",
      };
    }
    return { ok: true as const, message: "Feedback saved for profile training." };
  } catch (err) {
    return {
      ok: false as const,
      message: err instanceof Error ? err.message : String(err),
    };
  }
}

export async function submitHighlightFeedbackAction(input: {
  projectId: string;
  highlightId: string;
  profileId?: string | null;
  startSeconds: number;
  endSeconds: number;
  vote: "accepted" | "rejected";
  reason?: HighlightReason | null;
  editorNotes?: import("@/lib/db/schema").TrainingEditorNotes;
}) {
  try {
    const result = await recordHighlightFeedback({
      projectId: input.projectId,
      highlightId: input.highlightId,
      profileId: input.profileId,
      startSeconds: input.startSeconds,
      endSeconds: input.endSeconds,
      label: input.vote,
      reasonSnapshot: input.reason ?? null,
      editorNotes: input.editorNotes,
      autoRetrain: true,
    });
    revalidatePath(`/projects/${input.projectId}`);
    if (!result.stored) {
      return {
        ok: false as const,
        message: "No highlight profile configured for this project.",
      };
    }
    return { ok: true as const, message: "Feedback saved for profile training." };
  } catch (err) {
    return {
      ok: false as const,
      message: err instanceof Error ? err.message : String(err),
    };
  }
}

export async function submitClipFeedbackAction(input: {
  projectId: string;
  clipId: string;
  highlightId: string;
  overallVote?: "up" | "down";
  signalVotes?: ClipSignalVotes;
  highlightStart: number;
  highlightEnd: number;
  sourceStart?: number | null;
  sourceEnd?: number | null;
  reason?: HighlightReason | null;
}) {
  const parsed = clipFeedbackSchema.safeParse(input);
  if (!parsed.success) {
    return {
      ok: false as const,
      message: parsed.error.issues.map((i) => i.message).join("; "),
    };
  }

  try {
    const result = await recordClipFeedback({
      clipId: parsed.data.clipId,
      highlightId: parsed.data.highlightId,
      projectId: parsed.data.projectId,
      overallVote: parsed.data.overallVote,
      signalVotes: (parsed.data.signalVotes ?? {}) as ClipSignalVotes,
      highlightStart: parsed.data.highlightStart,
      highlightEnd: parsed.data.highlightEnd,
      sourceStart: parsed.data.sourceStart ?? undefined,
      sourceEnd: parsed.data.sourceEnd ?? undefined,
      reason: (parsed.data.reason as HighlightReason) ?? null,
    });
    revalidatePath(`/projects/${parsed.data.projectId}`);

    if (parsed.data.overallVote) {
      await recordHighlightFeedback({
        projectId: parsed.data.projectId,
        highlightId: parsed.data.highlightId,
        startSeconds:
          parsed.data.sourceStart ?? parsed.data.highlightStart,
        endSeconds: parsed.data.sourceEnd ?? parsed.data.highlightEnd,
        label: parsed.data.overallVote === "up" ? "accepted" : "rejected",
        reasonSnapshot: (parsed.data.reason as HighlightReason) ?? null,
        autoRetrain: true,
      }).catch(() => undefined);
    }

    return {
      ok: true as const,
      learnedPreRoll: result.learnedPreRollSeconds,
      learnedTail: result.learnedTailPaddingSeconds,
    };
  } catch (err) {
    return {
      ok: false as const,
      message: err instanceof Error ? err.message : String(err),
    };
  }
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

  const tagsArray = sanitizeYouTubeTags(
    (data.tags || "")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean),
  );

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

// -------------------------------------------------------------------------
// Re-analyze with an optional per-run model override.
// -------------------------------------------------------------------------

export type ReanalyzeResult =
  | { ok: true; message: string; jobId: string; modelUsed: string }
  | { ok: false; message: string };

const reanalyzeSchema = z.object({
  projectId: z.string().min(1),
  // Empty string / null = "use whatever's saved on the project"; the worker
  // will fall back to the project's `analyzeModel` setting.
  modelOverride: z
    .union([z.literal("pro"), z.literal("flash"), z.literal("")])
    .optional(),
  /** Per-run creator brief passed to Gemini (maps to project `vibe`). */
  vibeOverride: z.string().max(500).optional(),
  /** Also persist the override to the project's saved settings. */
  persistAsDefault: z.boolean().optional(),
  persistVibeAsDefault: z.boolean().optional(),
  reanalysisMode: z.enum(["local_only", "visual_only", "full"]).optional(),
});

/**
 * Manually trigger a fresh analyze run on the project's existing transcript.
 * Useful for A/B testing Pro vs Flash on the same content without paying for
 * a re-transcription.
 *
 * The flow:
 *   1. Validate that the project has a transcript (analyze depends on it).
 *   2. Optionally update the saved `analyzeModel` setting (persistAsDefault).
 *   3. Delete the project's existing highlights so the new run starts clean.
 *      (Clips already rendered from old highlights stay — only the highlight
 *      rows are dropped. The cascading FK on highlight_id is SET NULL? No,
 *      it's CASCADE, so clips DO get deleted. We protect them by setting
 *      highlight_id NULL is not allowed, so we hard-skip clip deletion by
 *      only deleting highlights that have no clip rows attached.)
 *   4. Flip project.status to "analyzing" and enqueue an `analyze` job with
 *      `analyze_model_override` in the payload.
 */
export async function reanalyzeProjectAction(input: {
  projectId: string;
  modelOverride?: "pro" | "flash" | "";
  vibeOverride?: string;
  persistAsDefault?: boolean;
  persistVibeAsDefault?: boolean;
  reanalysisMode?: "local_only" | "visual_only" | "full";
}): Promise<ReanalyzeResult> {
  const parsed = reanalyzeSchema.safeParse(input);
  if (!parsed.success) {
    return { ok: false, message: "Bad request shape." };
  }
  const {
    projectId,
    modelOverride,
    vibeOverride,
    persistAsDefault,
    persistVibeAsDefault,
    reanalysisMode,
  } = parsed.data;

  const project = db
    .select()
    .from(schema.projects)
    .where(eq(schema.projects.id, projectId))
    .limit(1)
    .all()[0];
  if (!project) {
    return { ok: false, message: "Project not found." };
  }

  const video = db
    .select()
    .from(schema.videos)
    .where(eq(schema.videos.projectId, projectId))
    .limit(1)
    .all()[0];
  if (!video) {
    return {
      ok: false,
      message: "Project has no video yet. Run ingest first.",
    };
  }

  const transcript = db
    .select({ id: schema.transcripts.id })
    .from(schema.transcripts)
    .where(eq(schema.transcripts.videoId, video.id))
    .limit(1)
    .all()[0];
  if (!transcript) {
    return {
      ok: false,
      message:
        "Project has no transcript yet. Wait for the transcribe step (or upload your own).",
    };
  }

  // Optionally make the override stick for future analyze runs too.
  // Merge with DEFAULT_PROJECT_SETTINGS so older project rows (which may
  // not have the new analyzeModel / preRollSeconds fields) still satisfy
  // the ProjectSettings type when we write them back.
  const currentSettings: ProjectSettings = {
    ...DEFAULT_PROJECT_SETTINGS,
    ...((project.settingsJson as Partial<ProjectSettings>) ?? {}),
  };
  const savedModel: ProjectSettings["analyzeModel"] = normalizeAnalyzeModel(
    currentSettings.analyzeModel,
  );
  const savedVibe = currentSettings.vibe || "";
  const effectiveTier: ProjectSettings["analyzeModel"] =
    modelOverride && modelOverride.length > 0 ? modelOverride : savedModel;
  const effectiveModel: string =
    ANALYZE_MODEL_LABELS[effectiveTier] ?? effectiveTier;
  const trimmedVibe = vibeOverride?.trim() ?? "";
  const effectiveVibe =
    trimmedVibe.length > 0 ? trimmedVibe : savedVibe;

  const shouldPersistModel =
    persistAsDefault && modelOverride && modelOverride.length > 0;
  const shouldPersistVibe =
    persistVibeAsDefault && trimmedVibe.length > 0;

  if (shouldPersistModel || shouldPersistVibe) {
    const next: ProjectSettings = {
      ...currentSettings,
      ...(shouldPersistModel ? { analyzeModel: modelOverride } : {}),
      ...(shouldPersistVibe ? { vibe: trimmedVibe } : {}),
    };
    db.update(schema.projects)
      .set({
        settingsJson: next,
        updatedAt: new Date(),
      })
      .where(eq(schema.projects.id, projectId))
      .run();
  }

  // Drop the project's existing highlights so the new run gets a clean slate.
  // BUT: highlights have a CASCADE FK from clips → deleting a highlight wipes
  // its clip. We protect any highlight that's already been rendered to a clip
  // (the user may have published it). Only highlights with no clip rows are
  // dropped here.
  const allHighlights = db
    .select({ id: schema.highlights.id })
    .from(schema.highlights)
    .where(eq(schema.highlights.videoId, video.id))
    .all();

  if (allHighlights.length > 0) {
    const idList = allHighlights.map((h) => h.id);
    const clippedHighlightIds = new Set(
      db
        .selectDistinct({ highlightId: schema.clips.highlightId })
        .from(schema.clips)
        .where(inArray(schema.clips.highlightId, idList))
        .all()
        .map((r) => r.highlightId),
    );
    for (const h of allHighlights) {
      if (!clippedHighlightIds.has(h.id)) {
        db.delete(schema.highlights)
          .where(eq(schema.highlights.id, h.id))
          .run();
      }
    }
  }

  // Flip the project status so the UI shows the spinner straight away.
  db.update(schema.projects)
    .set({
      status: "analyzing",
      notes: [
        `Re-analyzing with ${effectiveModel}`,
        effectiveVibe ? `brief: "${effectiveVibe.slice(0, 80)}${effectiveVibe.length > 80 ? "…" : ""}"` : null,
        shouldPersistModel || shouldPersistVibe ? "(saved as default)" : "(one-off)",
      ]
        .filter(Boolean)
        .join(" · "),
      updatedAt: new Date(),
    })
    .where(eq(schema.projects.id, projectId))
    .run();

  const mode = reanalysisMode ?? "full";
  const twelvelabsEnabled = process.env.TWELVELABS_ENABLED === "true";
  const useVisualPass =
    twelvelabsEnabled && (mode === "visual_only" || mode === "full");
  const jobType = useVisualPass ? "twelvelabs_analyze" : "analyze";

  const job = await enqueueJob({
    type: jobType,
    project_id: projectId,
    payload: {
      project_id: projectId,
      video_id: video.id,
      reanalysis_mode: mode,
      analyze_model_override:
        modelOverride && modelOverride.length > 0 ? modelOverride : undefined,
      vibe_override: trimmedVibe.length > 0 ? trimmedVibe : undefined,
    },
  });

  revalidatePath(`/projects/${projectId}`);
  return {
    ok: true,
    message: `Analyzing with ${effectiveModel}${effectiveVibe ? ` — looking for: "${effectiveVibe.slice(0, 60)}${effectiveVibe.length > 60 ? "…" : ""}"` : ""}. Usually 5–30 seconds.`,
    jobId: job.id,
    modelUsed: effectiveModel,
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

// -------------------------------------------------------------------------
// Custom transcript upload (SRT / VTT / JSON).
// -------------------------------------------------------------------------

export type UploadTranscriptResult =
  | {
      ok: true;
      message: string;
      segmentCount: number;
      format: string;
    }
  | { ok: false; message: string };

/**
 * Replace the auto-generated transcript for a project with a user-supplied
 * one (SRT, VTT, or JSON). The existing transcript + transcript_segments
 * rows are deleted (cascade-safe) and replaced. Any previously generated
 * highlights are also cleared so the next analyze run starts fresh.
 *
 * Limits: 5 MB file size, 100k segments — both well above any sane VTT.
 */
const MAX_TRANSCRIPT_BYTES = 5 * 1024 * 1024;
const MAX_TRANSCRIPT_SEGMENTS = 100_000;

export async function uploadTranscriptAction(input: {
  projectId: string;
  filename: string;
  content: string;
  rerunAnalyze: boolean;
}): Promise<UploadTranscriptResult> {
  const { projectId, filename, content, rerunAnalyze } = input;
  if (!projectId || !content) {
    return { ok: false, message: "Missing projectId or content." };
  }
  if (content.length > MAX_TRANSCRIPT_BYTES) {
    return {
      ok: false,
      message: `Transcript file is over the ${MAX_TRANSCRIPT_BYTES / 1024 / 1024} MB limit.`,
    };
  }

  // Look up the project + video so we know what to attach the transcript to.
  const project = db
    .select()
    .from(schema.projects)
    .where(eq(schema.projects.id, projectId))
    .limit(1)
    .all()[0];
  if (!project) return { ok: false, message: "Project not found." };

  const video = db
    .select()
    .from(schema.videos)
    .where(eq(schema.videos.projectId, projectId))
    .limit(1)
    .all()[0];
  if (!video) {
    return {
      ok: false,
      message:
        "This project has no video yet. Run ingest first, then upload a transcript.",
    };
  }

  let parsed;
  try {
    parsed = parseTranscript(filename, content);
  } catch (err) {
    const msg =
      err instanceof TranscriptParseError
        ? err.message
        : err instanceof Error
          ? err.message
          : String(err);
    return { ok: false, message: msg };
  }
  if (parsed.segments.length > MAX_TRANSCRIPT_SEGMENTS) {
    return {
      ok: false,
      message: `Transcript has ${parsed.segments.length} segments (max ${MAX_TRANSCRIPT_SEGMENTS}).`,
    };
  }

  // Delete prior transcript (CASCADE drops segments) AND any highlights
  // that were built off the old transcript — they'd reference text that no
  // longer exists.
  db.delete(schema.transcripts)
    .where(eq(schema.transcripts.videoId, video.id))
    .run();
  db.delete(schema.highlights)
    .where(eq(schema.highlights.videoId, video.id))
    .run();

  // Insert the new transcript + its segments in a single transaction.
  const fullText = parsed.segments
    .map((s) => s.text.trim())
    .filter(Boolean)
    .join(" ");

  const transcriptId = db
    .insert(schema.transcripts)
    .values({
      videoId: video.id,
      language: parsed.language,
      model: `user-upload:${parsed.format}`,
      fullText,
    })
    .returning({ id: schema.transcripts.id })
    .all()[0].id;

  // Batch insert segments. SQLite has a 999 parameter limit by default; we
  // insert in chunks of 200 segments (≤4 columns each) to stay well under.
  const CHUNK = 200;
  for (let i = 0; i < parsed.segments.length; i += CHUNK) {
    const slice = parsed.segments.slice(i, i + CHUNK);
    db.insert(schema.transcriptSegments)
      .values(
        slice.map((s) => ({
          transcriptId,
          startSeconds: s.startSeconds,
          endSeconds: s.endSeconds,
          text: s.text,
          wordsJson: s.words
            ? s.words.map((w) => ({
                word: w.word,
                start: w.start,
                end: w.end,
                confidence: 1.0,
              }))
            : null,
        })),
      )
      .run();
  }

  // Reset project state so the user sees the new transcript reflected.
  db.update(schema.projects)
    .set({
      status: rerunAnalyze ? "analyzing" : "ready",
      notes: `Transcript replaced with user ${parsed.format.toUpperCase()} (${parsed.segments.length} segments).`,
      updatedAt: new Date(),
    })
    .where(eq(schema.projects.id, projectId))
    .run();

  if (rerunAnalyze) {
    await enqueueJob({
      type: "analyze",
      project_id: projectId,
      payload: { project_id: projectId, video_id: video.id },
    });
  }

  revalidatePath(`/projects/${projectId}`);
  return {
    ok: true,
    message: rerunAnalyze
      ? `Replaced transcript (${parsed.segments.length} segments) and queued re-analysis.`
      : `Replaced transcript (${parsed.segments.length} segments).`,
    segmentCount: parsed.segments.length,
    format: parsed.format,
  };
}

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

const deleteProjectSchema = z.object({
  projectId: z.string().min(1),
  confirm: z.literal("DELETE"),
});

export type DeleteProjectActionResult = {
  ok: boolean;
  message: string;
};

/**
 * Permanently delete a project: cancel jobs, wipe media on disk, cascade DB.
 */
export async function deleteProjectPermanentlyAction(input: {
  projectId: string;
  confirm: string;
}): Promise<DeleteProjectActionResult> {
  const parsed = deleteProjectSchema.safeParse({
    projectId: input.projectId,
    confirm: input.confirm.trim().toUpperCase(),
  });
  if (!parsed.success) {
    return {
      ok: false,
      message: 'Type DELETE (all caps) to confirm permanent deletion.',
    };
  }

  const result = deleteProjectPermanently(parsed.data.projectId);

  revalidatePath("/");
  revalidatePath("/admin");
  revalidatePath("/storage");
  revalidatePath(`/projects/${parsed.data.projectId}`);

  return { ok: result.ok, message: result.message };
}
