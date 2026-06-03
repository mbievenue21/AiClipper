/**
 * AiClipper database schema (Drizzle ORM, SQLite dialect).
 *
 * This file is the SINGLE SOURCE OF TRUTH for the database. Both the
 * Next.js app (via Drizzle) and the Python worker (via SQLAlchemy models
 * defined in apps/worker/worker/db.py) read and write this same file.
 *
 * Conventions
 * -----------
 * - IDs are short nanoid strings (URL-safe, sortable enough for our use).
 * - Timestamps are stored as Unix-epoch milliseconds (integer) so they
 *   work uniformly across JavaScript Date and Python datetime.
 * - "json" columns are stored as TEXT and parsed at read time via Drizzle's
 *   { mode: 'json' } helper.
 * - Foreign keys use ON DELETE CASCADE so deleting a project cleans up
 *   all its videos, highlights, clips, etc.
 */
import { sql } from "drizzle-orm";
import {
  index,
  integer,
  real,
  sqliteTable,
  text,
  uniqueIndex,
} from "drizzle-orm/sqlite-core";
import { nanoid } from "nanoid";

const id = () =>
  text("id")
    .primaryKey()
    .$defaultFn(() => nanoid(12));

const createdAt = () =>
  integer("created_at", { mode: "timestamp_ms" })
    .notNull()
    .default(sql`(unixepoch() * 1000)`);

const updatedAt = () =>
  integer("updated_at", { mode: "timestamp_ms" })
    .notNull()
    .default(sql`(unixepoch() * 1000)`)
    .$onUpdate(() => new Date());

// ============================================================================
// PROJECTS
// A "project" is one source video we want to clip. It groups everything else.
// ============================================================================
export const projects = sqliteTable("projects", {
  id: id(),
  name: text("name").notNull(),
  sourceUrl: text("source_url"),
  sourceType: text("source_type", {
    enum: ["youtube", "twitch", "upload"],
  }).notNull(),
  // Top-level status reflects the latest pipeline stage we've reached.
  status: text("status", {
    enum: [
      "pending",
      "ingesting",
      "transcribing",
      "analyzing",
      "ready",
      "failed",
    ],
  })
    .notNull()
    .default("pending"),
  notes: text("notes"),
  createdAt: createdAt(),
  updatedAt: updatedAt(),
});

// ============================================================================
// VIDEOS
// The actual downloaded source file on disk. One project = one source video
// for now (we can relax this later for multi-VOD projects).
// ============================================================================
export const videos = sqliteTable(
  "videos",
  {
    id: id(),
    projectId: text("project_id")
      .notNull()
      .references(() => projects.id, { onDelete: "cascade" }),
    filePath: text("file_path").notNull(), // relative to MEDIA_ROOT
    durationSeconds: real("duration_seconds"),
    width: integer("width"),
    height: integer("height"),
    fps: real("fps"),
    codec: text("codec"),
    sizeBytes: integer("size_bytes"),
    // Path to extracted mono 16kHz WAV used for transcription + audio analysis.
    audioPath: text("audio_path"),
    // Raw chat replay JSON path (Twitch/YouTube live). Null for uploads.
    chatJsonPath: text("chat_json_path"),
    createdAt: createdAt(),
  },
  (t) => [index("videos_project_idx").on(t.projectId)],
);

// ============================================================================
// TRANSCRIPTS
// One per video. Big-picture metadata; the actual segments live below.
// ============================================================================
export const transcripts = sqliteTable(
  "transcripts",
  {
    id: id(),
    videoId: text("video_id")
      .notNull()
      .references(() => videos.id, { onDelete: "cascade" }),
    language: text("language"),
    model: text("model"), // e.g. "faster-whisper:large-v3"
    fullText: text("full_text"),
    createdAt: createdAt(),
  },
  (t) => [uniqueIndex("transcripts_video_unique").on(t.videoId)],
);

// ============================================================================
// TRANSCRIPT SEGMENTS
// Per-utterance rows with start/end seconds. Word-level data lives in
// `wordsJson` so we can render karaoke-style captions in Remotion.
// Indexed by (transcript_id, start_seconds) for fast range queries.
// ============================================================================
export type WordTiming = {
  word: string;
  start: number; // seconds
  end: number;
  confidence: number;
};

export const transcriptSegments = sqliteTable(
  "transcript_segments",
  {
    id: id(),
    transcriptId: text("transcript_id")
      .notNull()
      .references(() => transcripts.id, { onDelete: "cascade" }),
    startSeconds: real("start_seconds").notNull(),
    endSeconds: real("end_seconds").notNull(),
    text: text("text").notNull(),
    wordsJson: text("words_json", { mode: "json" }).$type<WordTiming[]>(),
  },
  (t) => [
    index("transcript_segments_transcript_idx").on(
      t.transcriptId,
      t.startSeconds,
    ),
  ],
);

// ============================================================================
// CHAT EVENTS
// Parsed messages from VOD chat replay. Used to compute per-second chat
// density, which is one of the strongest highlight signals for streams.
// ============================================================================
export const chatEvents = sqliteTable(
  "chat_events",
  {
    id: id(),
    videoId: text("video_id")
      .notNull()
      .references(() => videos.id, { onDelete: "cascade" }),
    timestampSeconds: real("timestamp_seconds").notNull(),
    username: text("username"),
    message: text("message"),
    emoteCount: integer("emote_count").notNull().default(0),
    messageType: text("message_type"), // chat | sub | bits | raid | etc.
  },
  (t) => [
    index("chat_events_video_time_idx").on(t.videoId, t.timestampSeconds),
  ],
);

// ============================================================================
// AUDIO FEATURES
// Per-second audio energy + excitement score from librosa. Stored as one
// JSON blob (array of {t, rms_db, excitement}) on the video to keep it
// simple; we never query individual seconds, only the whole series.
// ============================================================================
export type AudioFeatureSample = {
  t: number; // second offset
  rmsDb: number;
  excitement: number; // 0..1
};

export const audioFeatures = sqliteTable("audio_features", {
  id: id(),
  videoId: text("video_id")
    .notNull()
    .references(() => videos.id, { onDelete: "cascade" })
    .unique(),
  samplesJson: text("samples_json", { mode: "json" })
    .$type<AudioFeatureSample[]>()
    .notNull(),
  createdAt: createdAt(),
});

// ============================================================================
// HIGHLIGHTS
// Candidate clip ranges produced by the analysis step. A highlight is
// "approved" by the user clicking a button; that triggers clip rendering.
// `reasonJson` captures which signals contributed (chat / audio / LLM)
// so we can show *why* the AI picked this moment.
// ============================================================================
export type HighlightReason = {
  chatScore: number;
  audioScore: number;
  llmScore: number;
  llmExplanation: string;
  signals: string[]; // e.g. ["chat_spike", "laughter", "key_phrase"]
};

export const highlights = sqliteTable(
  "highlights",
  {
    id: id(),
    videoId: text("video_id")
      .notNull()
      .references(() => videos.id, { onDelete: "cascade" }),
    startSeconds: real("start_seconds").notNull(),
    endSeconds: real("end_seconds").notNull(),
    score: real("score").notNull(), // composite 0..1
    title: text("title"),
    summary: text("summary"),
    reasonJson: text("reason_json", { mode: "json" }).$type<HighlightReason>(),
    status: text("status", {
      enum: ["candidate", "approved", "rejected", "rendered"],
    })
      .notNull()
      .default("candidate"),
    createdAt: createdAt(),
  },
  (t) => [index("highlights_video_score_idx").on(t.videoId, t.score)],
);

// ============================================================================
// CLIPS
// The actual rendered video file for an approved highlight. We may render
// multiple variants of the same highlight (horizontal + vertical + with
// captions vs without), each row representing one output file.
// ============================================================================
export const clips = sqliteTable(
  "clips",
  {
    id: id(),
    highlightId: text("highlight_id")
      .notNull()
      .references(() => highlights.id, { onDelete: "cascade" }),
    filePath: text("file_path").notNull(),
    thumbnailPath: text("thumbnail_path"),
    durationSeconds: real("duration_seconds"),
    aspect: text("aspect", { enum: ["16:9", "9:16", "1:1"] }).notNull(),
    hasCaptions: integer("has_captions", { mode: "boolean" })
      .notNull()
      .default(false),
    createdAt: createdAt(),
  },
  (t) => [index("clips_highlight_idx").on(t.highlightId)],
);

// ============================================================================
// ACCOUNTS
// OAuth tokens for connected social platforms. The Python worker reads
// these when it's time to upload.
// ============================================================================
export const accounts = sqliteTable(
  "accounts",
  {
    id: id(),
    platform: text("platform", {
      enum: ["youtube", "tiktok", "instagram"],
    }).notNull(),
    label: text("label").notNull(), // human-friendly: "Main YouTube channel"
    accessToken: text("access_token").notNull(),
    refreshToken: text("refresh_token"),
    expiresAt: integer("expires_at", { mode: "timestamp_ms" }),
    rawJson: text("raw_json", { mode: "json" }).$type<Record<string, unknown>>(),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    uniqueIndex("accounts_platform_label_unique").on(t.platform, t.label),
  ],
);

// ============================================================================
// SCHEDULED UPLOADS
// A clip slated to be uploaded to a platform at a given time.
// The worker scans this table periodically and uploads when `scheduledFor`
// is in the past and status is still "pending".
// ============================================================================
export const scheduledUploads = sqliteTable(
  "scheduled_uploads",
  {
    id: id(),
    clipId: text("clip_id")
      .notNull()
      .references(() => clips.id, { onDelete: "cascade" }),
    accountId: text("account_id")
      .notNull()
      .references(() => accounts.id, { onDelete: "cascade" }),
    title: text("title").notNull(),
    description: text("description"),
    tagsJson: text("tags_json", { mode: "json" }).$type<string[]>(),
    scheduledFor: integer("scheduled_for", { mode: "timestamp_ms" }).notNull(),
    status: text("status", {
      enum: ["pending", "uploading", "uploaded", "failed"],
    })
      .notNull()
      .default("pending"),
    externalId: text("external_id"), // returned by platform after upload
    externalUrl: text("external_url"),
    errorMessage: text("error_message"),
    createdAt: createdAt(),
    updatedAt: updatedAt(),
  },
  (t) => [
    index("scheduled_uploads_due_idx").on(t.status, t.scheduledFor),
  ],
);

// ============================================================================
// JOBS
// Our SQLite-backed job queue. The Python worker polls for `pending` rows
// using `BEGIN IMMEDIATE` to claim them atomically. Each job has a `type`
// that maps to a Python handler (ingest, transcribe, analyze, render, publish).
// `payloadJson` is the type-specific input. `resultJson` is the output.
// ============================================================================
export type JobType =
  | "ingest"
  | "transcribe"
  | "analyze"
  | "render"
  | "publish";

export type JobStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled";

export const jobs = sqliteTable(
  "jobs",
  {
    id: id(),
    type: text("type").$type<JobType>().notNull(),
    projectId: text("project_id").references(() => projects.id, {
      onDelete: "cascade",
    }),
    payloadJson: text("payload_json", { mode: "json" })
      .$type<Record<string, unknown>>()
      .notNull(),
    status: text("status").$type<JobStatus>().notNull().default("pending"),
    progress: real("progress").notNull().default(0), // 0..1
    progressMessage: text("progress_message"),
    attempts: integer("attempts").notNull().default(0),
    maxAttempts: integer("max_attempts").notNull().default(3),
    // If set, this job won't be picked up until the dependency succeeds.
    dependsOnJobId: text("depends_on_job_id"),
    resultJson: text("result_json", { mode: "json" }).$type<
      Record<string, unknown>
    >(),
    errorMessage: text("error_message"),
    createdAt: createdAt(),
    startedAt: integer("started_at", { mode: "timestamp_ms" }),
    finishedAt: integer("finished_at", { mode: "timestamp_ms" }),
  },
  (t) => [
    index("jobs_status_created_idx").on(t.status, t.createdAt),
    index("jobs_project_idx").on(t.projectId),
  ],
);

// ============================================================================
// Type exports for use throughout the app.
// ============================================================================
export type Project = typeof projects.$inferSelect;
export type NewProject = typeof projects.$inferInsert;
export type Video = typeof videos.$inferSelect;
export type Highlight = typeof highlights.$inferSelect;
export type Clip = typeof clips.$inferSelect;
export type Job = typeof jobs.$inferSelect;
export type NewJob = typeof jobs.$inferInsert;
export type Account = typeof accounts.$inferSelect;
export type ScheduledUpload = typeof scheduledUploads.$inferSelect;
