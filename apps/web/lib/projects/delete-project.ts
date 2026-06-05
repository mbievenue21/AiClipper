/**
 * Permanently remove a project: cancel in-flight jobs, delete on-disk media,
 * then cascade-delete all DB rows (videos, transcripts, highlights, clips,
 * TwelveLabs index, visual segments, scheduled uploads, etc.).
 */
import fs from "node:fs";
import path from "node:path";
import { and, eq, inArray } from "drizzle-orm";

import { db, schema } from "@/lib/db/client";

export type DeleteProjectResult = {
  ok: boolean;
  message: string;
  projectId: string;
  jobsCancelled?: number;
  dbRowsDeleted?: number;
  filesDeleted?: number;
  bytesReclaimed?: number;
};

function resolveMediaRoot(): string {
  const raw = process.env.MEDIA_ROOT ?? "./data/videos";
  const repoRoot = path.resolve(process.cwd(), "..", "..");
  return path.isAbsolute(raw) ? raw : path.resolve(repoRoot, raw);
}

function inside(parent: string, target: string): boolean {
  const rel = path.relative(parent, target);
  return !rel.startsWith("..") && !path.isAbsolute(rel);
}

function unlinkSafe(p: string): number {
  try {
    const s = fs.statSync(p);
    if (!s.isFile()) return 0;
    fs.unlinkSync(p);
    return s.size;
  } catch {
    return 0;
  }
}

/** Recursive delete of a directory tree; returns file count + bytes reclaimed. */
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
  try {
    fs.rmSync(target, { recursive: true, force: true });
  } catch {
    // best effort
  }
  return { files, bytes };
}

function formatBytes(n: number): string {
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

/**
 * Cancel pending/running jobs, wipe `data/videos/<projectId>/`, delete the
 * project row (FK cascade removes all related records).
 */
export function deleteProjectPermanently(projectId: string): DeleteProjectResult {
  const id = projectId.trim();
  if (!id) {
    return { ok: false, message: "Project id is required.", projectId: "" };
  }

  const project = db
    .select({ id: schema.projects.id, name: schema.projects.name })
    .from(schema.projects)
    .where(eq(schema.projects.id, id))
    .limit(1)
    .all()[0];

  if (!project) {
    return {
      ok: false,
      message: `Project ${id} not found.`,
      projectId: id,
    };
  }

  const jobsCancelled = db
    .update(schema.jobs)
    .set({
      status: "cancelled",
      progressMessage: "cancelled — project deleted",
      finishedAt: new Date(),
    })
    .where(
      and(
        eq(schema.jobs.projectId, id),
        inArray(schema.jobs.status, ["pending", "running"]),
      ),
    )
    .run().changes;

  const root = resolveMediaRoot();
  const mediaDir = path.resolve(root, id);
  let filesDeleted = 0;
  let bytesReclaimed = 0;
  if (inside(root, mediaDir) && fs.existsSync(mediaDir)) {
    const r = rmrf(mediaDir);
    filesDeleted = r.files;
    bytesReclaimed = r.bytes;
  }

  const dbRowsDeleted = db
    .delete(schema.projects)
    .where(eq(schema.projects.id, id))
    .run().changes;

  const parts: string[] = [
    `Deleted "${project.name}"`,
    `${dbRowsDeleted} project row`,
  ];
  if (jobsCancelled > 0) {
    parts.push(`${jobsCancelled} job(s) cancelled`);
  }
  if (filesDeleted > 0) {
    parts.push(`${filesDeleted} file(s) (${formatBytes(bytesReclaimed)}) removed from disk`);
  }

  return {
    ok: dbRowsDeleted > 0,
    message: parts.join(" · ") + ".",
    projectId: id,
    jobsCancelled,
    dbRowsDeleted,
    filesDeleted,
    bytesReclaimed,
  };
}
