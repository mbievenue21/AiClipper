/**
 * Storage inventory + categorization for the /storage cleanup page.
 *
 * Walks MEDIA_ROOT and the Next.js dev cache, joins paths against the
 * Drizzle DB, and reports per-category byte counts plus a list of orphan
 * files (files on disk that no DB row references). All operations run
 * synchronously against better-sqlite3 + node:fs so we can use them
 * directly in Server Components without blocking on async I/O glue.
 *
 * Design notes
 * ------------
 * - Paths in the DB are stored POSIX-style relative to MEDIA_ROOT. We
 *   normalize disk paths to the same form for comparison.
 * - We never delete anything from this module — it's read-only. Server
 *   actions in app/storage/actions.ts do the actual writes.
 * - "Source videos can be deleted to free space, but the project can no
 *   longer be re-rendered" is communicated to the user via the action
 *   button copy, not enforced here.
 */
import "server-only";

import fs from "node:fs";
import path from "node:path";

import { db, schema } from "@/lib/db/client";

export type StorageCategory =
  | "sources"
  | "audio"
  | "chat"
  | "clips_raw"
  | "clips_captioned"
  | "thumbnails"
  | "subtitles"
  | "orphan"
  | "next_cache";

export type CategoryStat = {
  category: StorageCategory;
  label: string;
  description: string;
  files: number;
  bytes: number;
};

export type StorageReport = {
  mediaRoot: string;
  nextCacheRoot: string | null;
  totalBytes: number;
  totalFiles: number;
  categories: CategoryStat[];
  orphans: OrphanFile[];
  perProject: ProjectStorage[];
  dbBytes: number | null;
};

export type OrphanFile = {
  relPath: string; // relative to MEDIA_ROOT, POSIX
  absPath: string;
  bytes: number;
  mtimeMs: number;
};

export type ProjectStorage = {
  projectId: string;
  projectName: string | null;
  status: string;
  bytes: number;
  files: number;
  hasMedia: boolean;
};

const CATEGORY_LABELS: Record<StorageCategory, { label: string; description: string }> = {
  sources: {
    label: "Source videos",
    description:
      "Downloaded long-form videos (the input to the pipeline). Biggest files. Safe to delete once a project is `ready` if you don't plan to re-process.",
  },
  audio: {
    label: "Audio extracts",
    description:
      ".wav files extracted from sources for Whisper transcription. Only needed during the transcribe stage — disposable after.",
  },
  chat: {
    label: "Chat JSONs",
    description:
      "Twitch/YouTube live-chat replay dumps used for highlight scoring. Only needed during analyze — disposable after.",
  },
  clips_raw: {
    label: "Clips (uncaptioned)",
    description:
      "Original rendered clips. Safe to delete if you already have a captioned version.",
  },
  clips_captioned: {
    label: "Clips (captioned)",
    description:
      "Caption-burned-in clips. These are the actual upload artifacts — don't delete unless you're done with the project.",
  },
  thumbnails: {
    label: "Thumbnails",
    description: "JPEG/PNG previews. Small and cheap — keep these.",
  },
  subtitles: {
    label: "Subtitle files",
    description: ".ass / .srt files. Tiny, used by the caption burn-in step.",
  },
  orphan: {
    label: "Orphan files",
    description:
      "Files on disk that don't match any DB row. Almost always safe to delete (most are leftovers from failed renders or aborted jobs).",
  },
  next_cache: {
    label: "Next.js dev cache",
    description:
      "Turbopack's compiled-module cache (`apps/web/.next/dev/cache`). Wiping it forces a slower first reload but reclaims hundreds of MB.",
  },
};

function resolveMediaRoot(): string {
  const raw = process.env.MEDIA_ROOT ?? "./data/videos";
  const repoRoot = path.resolve(process.cwd(), "..", "..");
  return path.isAbsolute(raw) ? raw : path.resolve(repoRoot, raw);
}

function resolveNextCacheRoot(): string | null {
  // We're already running INSIDE apps/web for `pnpm dev`. cwd is therefore
  // apps/web — joining `.next/dev/cache` matches Turbopack's layout.
  const candidate = path.resolve(process.cwd(), ".next", "dev", "cache");
  return fs.existsSync(candidate) ? candidate : null;
}

function toPosix(rel: string): string {
  return rel.split(path.sep).join("/");
}

function safeStat(p: string): fs.Stats | null {
  try {
    return fs.statSync(p);
  } catch {
    return null;
  }
}

/**
 * Recursively walk `root` and call `fn(absPath, stat)` for every regular
 * file. Hidden dot-files are included so we can find stray .DS_Store etc.
 */
function walkFiles(
  root: string,
  fn: (absPath: string, stat: fs.Stats) => void,
): void {
  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(root, { withFileTypes: true });
  } catch {
    return;
  }
  for (const ent of entries) {
    const abs = path.join(root, ent.name);
    if (ent.isDirectory()) {
      walkFiles(abs, fn);
    } else if (ent.isFile()) {
      const stat = safeStat(abs);
      if (stat) fn(abs, stat);
    }
  }
}

function dbFilePath(): string | null {
  const repoRoot = path.resolve(process.cwd(), "..", "..");
  const candidates = [
    path.resolve(repoRoot, "data", "app.db"),
    path.resolve(process.cwd(), "..", "..", "data", "app.db"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

/**
 * Build the full storage report. Synchronous & cheap for typical sizes
 * (a few thousand files, a few GB) — we only stat each file once and do
 * no expensive hashing.
 */
export function scanStorage(): StorageReport {
  const mediaRoot = resolveMediaRoot();
  const nextCacheRoot = resolveNextCacheRoot();

  // ----- DB-side path inventory -----
  const videos = db
    .select({
      projectId: schema.videos.projectId,
      filePath: schema.videos.filePath,
      audioPath: schema.videos.audioPath,
      chatJsonPath: schema.videos.chatJsonPath,
    })
    .from(schema.videos)
    .all();

  const clips = db
    .select({
      filePath: schema.clips.filePath,
      captionedFilePath: schema.clips.captionedFilePath,
      thumbnailPath: schema.clips.thumbnailPath,
    })
    .from(schema.clips)
    .all();

  const projects = db
    .select({
      id: schema.projects.id,
      name: schema.projects.name,
      status: schema.projects.status,
    })
    .from(schema.projects)
    .all();

  const projectsById = new Map(projects.map((p) => [p.id, p]));

  // rel POSIX → category
  const dbPathCategory = new Map<string, StorageCategory>();
  for (const v of videos) {
    if (v.filePath) dbPathCategory.set(toPosix(v.filePath), "sources");
    if (v.audioPath) dbPathCategory.set(toPosix(v.audioPath), "audio");
    if (v.chatJsonPath) dbPathCategory.set(toPosix(v.chatJsonPath), "chat");
  }
  for (const c of clips) {
    if (c.filePath) dbPathCategory.set(toPosix(c.filePath), "clips_raw");
    if (c.captionedFilePath)
      dbPathCategory.set(toPosix(c.captionedFilePath), "clips_captioned");
    if (c.thumbnailPath)
      dbPathCategory.set(toPosix(c.thumbnailPath), "thumbnails");
  }

  // ----- Disk walk -----
  const bytesByCategory: Record<StorageCategory, { files: number; bytes: number }> = {
    sources: { files: 0, bytes: 0 },
    audio: { files: 0, bytes: 0 },
    chat: { files: 0, bytes: 0 },
    clips_raw: { files: 0, bytes: 0 },
    clips_captioned: { files: 0, bytes: 0 },
    thumbnails: { files: 0, bytes: 0 },
    subtitles: { files: 0, bytes: 0 },
    orphan: { files: 0, bytes: 0 },
    next_cache: { files: 0, bytes: 0 },
  };

  const orphans: OrphanFile[] = [];
  const perProjectMap = new Map<string, ProjectStorage>();

  let totalBytes = 0;
  let totalFiles = 0;

  if (fs.existsSync(mediaRoot)) {
    walkFiles(mediaRoot, (abs, stat) => {
      const rel = toPosix(path.relative(mediaRoot, abs));
      totalBytes += stat.size;
      totalFiles += 1;

      // first path segment is the project id
      const firstSeg = rel.split("/")[0];
      if (firstSeg) {
        const project = projectsById.get(firstSeg);
        const entry = perProjectMap.get(firstSeg) ?? {
          projectId: firstSeg,
          projectName: project?.name ?? null,
          status: project?.status ?? "(unknown / orphan)",
          bytes: 0,
          files: 0,
          hasMedia: true,
        };
        entry.bytes += stat.size;
        entry.files += 1;
        perProjectMap.set(firstSeg, entry);
      }

      const explicit = dbPathCategory.get(rel);
      if (explicit) {
        bytesByCategory[explicit].files += 1;
        bytesByCategory[explicit].bytes += stat.size;
        return;
      }

      // Heuristic categorization for things not pinned to a DB row.
      const ext = path.extname(rel).toLowerCase();
      if (ext === ".ass" || ext === ".srt" || ext === ".vtt") {
        bytesByCategory.subtitles.files += 1;
        bytesByCategory.subtitles.bytes += stat.size;
        return;
      }

      bytesByCategory.orphan.files += 1;
      bytesByCategory.orphan.bytes += stat.size;
      orphans.push({
        relPath: rel,
        absPath: abs,
        bytes: stat.size,
        mtimeMs: stat.mtimeMs,
      });
    });
  }

  // Make sure every project from the DB shows up, even those with 0 disk usage.
  for (const p of projects) {
    if (!perProjectMap.has(p.id)) {
      perProjectMap.set(p.id, {
        projectId: p.id,
        projectName: p.name,
        status: p.status,
        bytes: 0,
        files: 0,
        hasMedia: false,
      });
    }
  }

  let nextCacheBytes = 0;
  let nextCacheFiles = 0;
  if (nextCacheRoot) {
    walkFiles(nextCacheRoot, (_abs, stat) => {
      nextCacheBytes += stat.size;
      nextCacheFiles += 1;
    });
    bytesByCategory.next_cache.bytes = nextCacheBytes;
    bytesByCategory.next_cache.files = nextCacheFiles;
  }

  const categories: CategoryStat[] = (
    Object.keys(bytesByCategory) as StorageCategory[]
  ).map((cat) => ({
    category: cat,
    label: CATEGORY_LABELS[cat].label,
    description: CATEGORY_LABELS[cat].description,
    files: bytesByCategory[cat].files,
    bytes: bytesByCategory[cat].bytes,
  }));

  // Sort orphans newest-first so the user sees fresh leftovers first.
  orphans.sort((a, b) => b.mtimeMs - a.mtimeMs);

  const perProject = Array.from(perProjectMap.values()).sort(
    (a, b) => b.bytes - a.bytes,
  );

  let dbBytes: number | null = null;
  const dbFile = dbFilePath();
  if (dbFile) {
    const s = safeStat(dbFile);
    dbBytes = s?.size ?? null;
  }

  return {
    mediaRoot,
    nextCacheRoot,
    totalBytes,
    totalFiles,
    categories,
    orphans,
    perProject,
    dbBytes,
  };
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
}
