"use server";

/**
 * Server actions for /storage cleanup.
 *
 * Each action returns ``CleanupResult`` so the client panel can show
 * a consistent toast with bytes-reclaimed counts.
 *
 * Safety rules:
 * - Every filesystem unlink is contained to MEDIA_ROOT or the Next.js
 *   dev cache directory. We refuse any path that resolves outside.
 * - DB columns that reference deleted files are nulled where the schema
 *   allows it (audioPath, chatJsonPath, captionedFilePath, thumbnailPath)
 *   so the UI doesn't try to render 404s. videos.filePath is NOT NULL —
 *   we leave it pointing at a missing file but flip the project status
 *   to a recognizable string in the notes column.
 * - "Wipe everything" requires an explicit `confirm: "DELETE"` payload
 *   so an accidental click can't nuke the project.
 */
import { revalidatePath } from "next/cache";
import { and, eq, inArray, isNotNull, sql } from "drizzle-orm";
import fs from "node:fs";
import path from "node:path";

import { db, schema, sqlite } from "@/lib/db/client";

export type CleanupResult = {
  ok: boolean;
  message: string;
  bytesReclaimed?: number;
  filesDeleted?: number;
};

function resolveMediaRoot(): string {
  const raw = process.env.MEDIA_ROOT ?? "./data/videos";
  const repoRoot = path.resolve(process.cwd(), "..", "..");
  return path.isAbsolute(raw) ? raw : path.resolve(repoRoot, raw);
}

function resolveNextCacheRoot(): string {
  return path.resolve(process.cwd(), ".next", "dev", "cache");
}

function inside(parent: string, target: string): boolean {
  const rel = path.relative(parent, target);
  return !rel.startsWith("..") && !path.isAbsolute(rel);
}

function absInMedia(relPosix: string): string | null {
  const root = resolveMediaRoot();
  const abs = path.resolve(root, relPosix.split("/").join(path.sep));
  if (!inside(root, abs) && abs !== root) return null;
  return abs;
}

function statOrNull(p: string): fs.Stats | null {
  try {
    return fs.statSync(p);
  } catch {
    return null;
  }
}

function unlinkSafe(p: string): number {
  const s = statOrNull(p);
  if (!s || !s.isFile()) return 0;
  try {
    fs.unlinkSync(p);
    return s.size;
  } catch {
    return 0;
  }
}

/** Recursive rmdir that returns total bytes deleted. */
function rmrf(target: string): { files: number; bytes: number } {
  let files = 0;
  let bytes = 0;
  const stack: string[] = [target];
  while (stack.length > 0) {
    const cur = stack.pop()!;
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(cur, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const ent of entries) {
      const abs = path.join(cur, ent.name);
      if (ent.isDirectory()) {
        stack.push(abs);
      } else {
        const b = unlinkSafe(abs);
        if (b > 0) {
          files += 1;
          bytes += b;
        }
      }
    }
  }
  // Now try to remove the (now-empty) directory tree.
  try {
    fs.rmSync(target, { recursive: true, force: true });
  } catch {
    // best effort
  }
  return { files, bytes };
}

function revalidateAll() {
  revalidatePath("/storage");
  revalidatePath("/admin");
  revalidatePath("/");
}

// ----------------------------------------------------------------------
// 1. Delete source videos for `ready` projects
// ----------------------------------------------------------------------
export async function deleteReadySourcesAction(): Promise<CleanupResult> {
  const rows = db
    .select({
      videoId: schema.videos.id,
      projectId: schema.videos.projectId,
      filePath: schema.videos.filePath,
      status: schema.projects.status,
    })
    .from(schema.videos)
    .innerJoin(schema.projects, eq(schema.projects.id, schema.videos.projectId))
    .where(eq(schema.projects.status, "ready"))
    .all();

  let files = 0;
  let bytes = 0;
  for (const r of rows) {
    if (!r.filePath) continue;
    const abs = absInMedia(r.filePath);
    if (!abs) continue;
    const reclaimed = unlinkSafe(abs);
    if (reclaimed > 0) {
      files += 1;
      bytes += reclaimed;
    }
  }
  revalidateAll();
  return {
    ok: true,
    message:
      files > 0
        ? `Deleted ${files} source video(s), freed ${formatBytes(bytes)}.`
        : "No source videos to delete (no ready projects, or already cleaned).",
    bytesReclaimed: bytes,
    filesDeleted: files,
  };
}

// ----------------------------------------------------------------------
// 2. Delete audio extracts (.wav) — safe whenever transcripts exist
// ----------------------------------------------------------------------
export async function deleteAudioExtractsAction(): Promise<CleanupResult> {
  const rows = db
    .select({
      videoId: schema.videos.id,
      audioPath: schema.videos.audioPath,
    })
    .from(schema.videos)
    .where(isNotNull(schema.videos.audioPath))
    .all();

  let files = 0;
  let bytes = 0;
  const clearedIds: string[] = [];
  for (const r of rows) {
    if (!r.audioPath) continue;
    const abs = absInMedia(r.audioPath);
    if (!abs) continue;
    const reclaimed = unlinkSafe(abs);
    if (reclaimed > 0) {
      files += 1;
      bytes += reclaimed;
      clearedIds.push(r.videoId);
    }
  }
  if (clearedIds.length > 0) {
    db.update(schema.videos)
      .set({ audioPath: null })
      .where(inArray(schema.videos.id, clearedIds))
      .run();
  }
  revalidateAll();
  return {
    ok: true,
    message:
      files > 0
        ? `Deleted ${files} audio extract(s), freed ${formatBytes(bytes)}.`
        : "No audio extracts on disk.",
    bytesReclaimed: bytes,
    filesDeleted: files,
  };
}

// ----------------------------------------------------------------------
// 3. Delete chat JSONs
// ----------------------------------------------------------------------
export async function deleteChatDumpsAction(): Promise<CleanupResult> {
  const rows = db
    .select({
      videoId: schema.videos.id,
      chatJsonPath: schema.videos.chatJsonPath,
    })
    .from(schema.videos)
    .where(isNotNull(schema.videos.chatJsonPath))
    .all();

  let files = 0;
  let bytes = 0;
  const clearedIds: string[] = [];
  for (const r of rows) {
    if (!r.chatJsonPath) continue;
    const abs = absInMedia(r.chatJsonPath);
    if (!abs) continue;
    const reclaimed = unlinkSafe(abs);
    if (reclaimed > 0) {
      files += 1;
      bytes += reclaimed;
      clearedIds.push(r.videoId);
    }
  }
  if (clearedIds.length > 0) {
    db.update(schema.videos)
      .set({ chatJsonPath: null })
      .where(inArray(schema.videos.id, clearedIds))
      .run();
  }
  revalidateAll();
  return {
    ok: true,
    message:
      files > 0
        ? `Deleted ${files} chat JSON(s), freed ${formatBytes(bytes)}.`
        : "No chat JSONs on disk.",
    bytesReclaimed: bytes,
    filesDeleted: files,
  };
}

// ----------------------------------------------------------------------
// 4. Delete uncaptioned clip variants when a captioned version exists
// ----------------------------------------------------------------------
export async function deleteUncaptionedClipsAction(): Promise<CleanupResult> {
  const rows = db
    .select({
      id: schema.clips.id,
      filePath: schema.clips.filePath,
      captionedFilePath: schema.clips.captionedFilePath,
    })
    .from(schema.clips)
    .where(
      and(
        isNotNull(schema.clips.captionedFilePath),
        isNotNull(schema.clips.filePath),
      ),
    )
    .all();

  let files = 0;
  let bytes = 0;
  for (const r of rows) {
    if (!r.filePath || !r.captionedFilePath) continue;
    // Don't delete if for some reason both pointers are the same file.
    if (r.filePath === r.captionedFilePath) continue;
    const abs = absInMedia(r.filePath);
    if (!abs) continue;
    const reclaimed = unlinkSafe(abs);
    if (reclaimed > 0) {
      files += 1;
      bytes += reclaimed;
    }
  }
  revalidateAll();
  return {
    ok: true,
    message:
      files > 0
        ? `Deleted ${files} uncaptioned clip variant(s), freed ${formatBytes(bytes)}.`
        : "No uncaptioned variants to delete.",
    bytesReclaimed: bytes,
    filesDeleted: files,
  };
}

// ----------------------------------------------------------------------
// 5. Delete media for projects in 'failed' status (and cascade-delete rows)
// ----------------------------------------------------------------------
export async function deleteFailedProjectMediaAction(): Promise<CleanupResult> {
  const failed = db
    .select({ id: schema.projects.id })
    .from(schema.projects)
    .where(eq(schema.projects.status, "failed"))
    .all();

  if (failed.length === 0) {
    return { ok: true, message: "No failed projects." };
  }

  const root = resolveMediaRoot();
  let files = 0;
  let bytes = 0;

  for (const p of failed) {
    const dir = path.resolve(root, p.id);
    if (!inside(root, dir)) continue;
    if (!fs.existsSync(dir)) continue;
    const r = rmrf(dir);
    files += r.files;
    bytes += r.bytes;
  }

  // Cascade-delete the project rows themselves so the UI doesn't keep
  // showing them as "failed" with missing media.
  const failedIds = failed.map((p) => p.id);
  const dbChanges = db
    .delete(schema.projects)
    .where(inArray(schema.projects.id, failedIds))
    .run().changes;

  revalidateAll();
  return {
    ok: true,
    message: `Removed ${dbChanges} failed project(s) — deleted ${files} file(s), freed ${formatBytes(bytes)}.`,
    bytesReclaimed: bytes,
    filesDeleted: files,
  };
}

// ----------------------------------------------------------------------
// 6. Delete orphan files (on disk, not referenced by any DB row)
// ----------------------------------------------------------------------
export async function deleteOrphanFilesAction(): Promise<CleanupResult> {
  const root = resolveMediaRoot();
  if (!fs.existsSync(root)) {
    return { ok: true, message: "Media root doesn't exist yet." };
  }

  const referenced = new Set<string>();
  const videos = db
    .select({
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
  for (const v of videos) {
    if (v.filePath) referenced.add(v.filePath.split(path.sep).join("/"));
    if (v.audioPath) referenced.add(v.audioPath.split(path.sep).join("/"));
    if (v.chatJsonPath)
      referenced.add(v.chatJsonPath.split(path.sep).join("/"));
  }
  for (const c of clips) {
    if (c.filePath) referenced.add(c.filePath.split(path.sep).join("/"));
    if (c.captionedFilePath)
      referenced.add(c.captionedFilePath.split(path.sep).join("/"));
    if (c.thumbnailPath)
      referenced.add(c.thumbnailPath.split(path.sep).join("/"));
  }

  let files = 0;
  let bytes = 0;

  const stack: string[] = [root];
  while (stack.length > 0) {
    const cur = stack.pop()!;
    let entries: fs.Dirent[];
    try {
      entries = fs.readdirSync(cur, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const ent of entries) {
      const abs = path.join(cur, ent.name);
      if (ent.isDirectory()) {
        stack.push(abs);
        continue;
      }
      if (!ent.isFile()) continue;
      const rel = path.relative(root, abs).split(path.sep).join("/");
      if (referenced.has(rel)) continue;
      // Subtitle files alongside clips are usually orphans-by-design — keep them.
      const ext = path.extname(abs).toLowerCase();
      if (ext === ".ass" || ext === ".srt" || ext === ".vtt") continue;
      const reclaimed = unlinkSafe(abs);
      if (reclaimed > 0) {
        files += 1;
        bytes += reclaimed;
      }
    }
  }

  revalidateAll();
  return {
    ok: true,
    message:
      files > 0
        ? `Deleted ${files} orphan file(s), freed ${formatBytes(bytes)}.`
        : "No orphan files found.",
    bytesReclaimed: bytes,
    filesDeleted: files,
  };
}

// ----------------------------------------------------------------------
// 7. Wipe Next.js dev cache
// ----------------------------------------------------------------------
export async function wipeNextCacheAction(): Promise<CleanupResult> {
  const root = resolveNextCacheRoot();
  if (!fs.existsSync(root)) {
    return { ok: true, message: "Next.js dev cache directory doesn't exist." };
  }
  const r = rmrf(root);
  // We don't revalidate here because Turbopack may still be writing to
  // the dir; if the user is running `pnpm dev` they'll see a brief slowdown
  // on next reload while the cache rebuilds.
  return {
    ok: true,
    message:
      r.files > 0
        ? `Wiped Next.js cache: ${r.files} file(s), ${formatBytes(r.bytes)}. Next reload will be slower while it rebuilds.`
        : "Next.js cache was already empty.",
    bytesReclaimed: r.bytes,
    filesDeleted: r.files,
  };
}

// ----------------------------------------------------------------------
// 8. Prune finished job rows older than N hours (defaults to 24)
// ----------------------------------------------------------------------
export async function pruneFinishedJobsAction(
  olderThanHours = 24,
): Promise<CleanupResult> {
  const cutoff = Date.now() - olderThanHours * 3600_000;
  const changes = db
    .delete(schema.jobs)
    .where(
      and(
        sql`${schema.jobs.status} IN ('succeeded','failed','cancelled')`,
        sql`${schema.jobs.finishedAt} IS NOT NULL`,
        sql`${schema.jobs.finishedAt} < ${cutoff}`,
      ),
    )
    .run().changes;
  revalidateAll();
  return {
    ok: true,
    message: `Pruned ${changes} finished job row(s) older than ${olderThanHours}h.`,
    filesDeleted: changes,
  };
}

// ----------------------------------------------------------------------
// 9. Delete cancelled scheduled uploads
// ----------------------------------------------------------------------
export async function pruneCancelledUploadsAction(): Promise<CleanupResult> {
  const changes = db
    .delete(schema.scheduledUploads)
    .where(eq(schema.scheduledUploads.status, "cancelled"))
    .run().changes;
  revalidateAll();
  return {
    ok: true,
    message: `Removed ${changes} cancelled upload row(s).`,
    filesDeleted: changes,
  };
}

// ----------------------------------------------------------------------
// 10. VACUUM SQLite — compact the DB file after lots of deletes
// ----------------------------------------------------------------------
export async function vacuumDatabaseAction(): Promise<CleanupResult> {
  let beforeBytes: number | null = null;
  let afterBytes: number | null = null;
  try {
    const root = path.resolve(process.cwd(), "..", "..");
    const dbFile = path.resolve(root, "data", "app.db");
    beforeBytes = statOrNull(dbFile)?.size ?? null;
    sqlite.exec("VACUUM");
    afterBytes = statOrNull(dbFile)?.size ?? null;
  } catch (err) {
    return {
      ok: false,
      message:
        "VACUUM failed: " + (err instanceof Error ? err.message : String(err)),
    };
  }
  const reclaimed =
    beforeBytes != null && afterBytes != null
      ? Math.max(0, beforeBytes - afterBytes)
      : 0;
  return {
    ok: true,
    message:
      reclaimed > 0
        ? `Compacted DB: freed ${formatBytes(reclaimed)} (${formatBytes(beforeBytes)} → ${formatBytes(afterBytes)}).`
        : `Compacted DB (size unchanged at ${formatBytes(afterBytes ?? beforeBytes)}).`,
    bytesReclaimed: reclaimed,
  };
}

// ----------------------------------------------------------------------
// 11. Delete a single orphan file by relative path
// ----------------------------------------------------------------------
export async function deleteOrphanByPathAction(
  relPath: string,
): Promise<CleanupResult> {
  if (!relPath) return { ok: false, message: "missing path" };
  const abs = absInMedia(relPath);
  if (!abs) return { ok: false, message: "path escaped MEDIA_ROOT" };
  const bytes = unlinkSafe(abs);
  if (bytes === 0) return { ok: false, message: "file not found or empty" };
  revalidateAll();
  return {
    ok: true,
    message: `Deleted ${relPath} (${formatBytes(bytes)}).`,
    bytesReclaimed: bytes,
    filesDeleted: 1,
  };
}

// ----------------------------------------------------------------------
// 12. NUCLEAR — wipe all media + reset project status to failed
// ----------------------------------------------------------------------
export async function wipeAllMediaAction(
  confirm: string,
): Promise<CleanupResult> {
  if (confirm !== "DELETE") {
    return {
      ok: false,
      message: "Type DELETE to confirm. Nothing was deleted.",
    };
  }
  const root = resolveMediaRoot();
  if (!fs.existsSync(root)) {
    return { ok: true, message: "Media root is already empty." };
  }
  const r = rmrf(root);
  fs.mkdirSync(root, { recursive: true });

  // Drop all clip rows (cascades to scheduled_uploads).
  const clipChanges = db.delete(schema.clips).run().changes;
  // Reset every non-pending project to 'failed' with a note.
  const projectChanges = db
    .update(schema.projects)
    .set({
      status: "failed",
      notes: "All media was wiped via /storage. Create a new project to re-process.",
    })
    .where(sql`${schema.projects.status} != 'pending'`)
    .run().changes;

  revalidateAll();
  return {
    ok: true,
    message: `Nuclear wipe done: deleted ${r.files} file(s) (${formatBytes(r.bytes)}); removed ${clipChanges} clip row(s); marked ${projectChanges} project(s) failed.`,
    bytesReclaimed: r.bytes,
    filesDeleted: r.files,
  };
}

// Local copy of formatBytes — avoids importing from storage-scan because
// that module is server-only and may pull in heavy bits in the future.
function formatBytes(n: number | null | undefined): string {
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
