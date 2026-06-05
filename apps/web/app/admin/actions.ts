"use server";

import { revalidatePath } from "next/cache";
import { and, eq, inArray, sql } from "drizzle-orm";

import { db, schema } from "@/lib/db/client";
import { deleteProjectPermanently } from "@/lib/projects/delete-project";
import { callWorkerAdmin } from "@/lib/worker";

export type AdminActionResult = {
  ok: boolean;
  message: string;
  details?: Record<string, number>;
};

/**
 * Reset stuck `running` jobs back to `pending` and mark orphan projects
 * (mid-pipeline with no active jobs) as `failed`. Goes through the worker
 * so we use the same code path the runner uses at startup. If the worker
 * is unreachable, fall back to writing the DB directly from here.
 */
export async function healWorkerAction(): Promise<AdminActionResult> {
  try {
    const result = await callWorkerAdmin("heal");
    revalidatePath("/admin");
    revalidatePath("/");
    return {
      ok: true,
      message: `Healed via worker: reset ${result.jobs_reset} stuck job(s) and ${result.projects_healed} orphan project(s).`,
      details: result,
    };
  } catch (workerErr) {
    const fallback = await healDirectly();
    revalidatePath("/admin");
    revalidatePath("/");
    const reason =
      workerErr instanceof Error ? workerErr.message : String(workerErr);
    return {
      ok: true,
      message: `Worker unreachable (${reason}). Healed DB directly: reset ${fallback.jobs_reset} job(s) and ${fallback.projects_healed} project(s).`,
      details: fallback,
    };
  }
}

/** Same logic as worker's queue.heal_orphan_projects + reset_stuck_running_jobs, in JS. */
async function healDirectly(): Promise<{
  jobs_reset: number;
  projects_healed: number;
}> {
  const jobs_reset = db
    .update(schema.jobs)
    .set({
      status: "pending",
      progress: 0,
      progressMessage: "reset by admin (worker unreachable)",
      startedAt: null,
    })
    .where(eq(schema.jobs.status, "running"))
    .run().changes;

  // Mark projects mid-pipeline with NO active or succeeded jobs as failed.
  const candidates = db
    .select({ id: schema.projects.id })
    .from(schema.projects)
    .leftJoin(schema.jobs, eq(schema.jobs.projectId, schema.projects.id))
    .where(
      sql`${schema.projects.status} IN ('pending','ingesting','transcribing','analyzing')`,
    )
    .groupBy(schema.projects.id)
    .having(
      sql`SUM(CASE WHEN ${schema.jobs.status} IN ('pending','running') THEN 1 ELSE 0 END) = 0
          AND SUM(CASE WHEN ${schema.jobs.status} = 'succeeded' THEN 1 ELSE 0 END) = 0`,
    )
    .all();

  const ids = candidates.map((c) => c.id);
  let projects_healed = 0;
  if (ids.length > 0) {
    projects_healed = db
      .update(schema.projects)
      .set({
        status: "failed",
        notes: "Worker was unavailable when this stage ran. Delete this project and create a new one.",
      })
      .where(inArray(schema.projects.id, ids))
      .run().changes;
  }
  return { jobs_reset, projects_healed };
}

/** Cancel every pending job. Use to abort the queue. */
export async function cancelPendingAction(): Promise<AdminActionResult> {
  try {
    const result = await callWorkerAdmin("cancel-pending");
    revalidatePath("/admin");
    revalidatePath("/");
    return {
      ok: true,
      message: `Cancelled ${result.pending_cancelled} pending job(s).`,
      details: result,
    };
  } catch {
    const changes = db
      .update(schema.jobs)
      .set({ status: "cancelled", progressMessage: "cancelled by admin" })
      .where(eq(schema.jobs.status, "pending"))
      .run().changes;
    revalidatePath("/admin");
    revalidatePath("/");
    return {
      ok: true,
      message: `Cancelled ${changes} pending job(s) (worker unreachable, fell back to DB).`,
    };
  }
}

/** Delete all projects in the `failed` status (media + DB cascade). */
export async function deleteFailedProjectsAction(): Promise<AdminActionResult> {
  const failedIds = db
    .select({ id: schema.projects.id })
    .from(schema.projects)
    .where(eq(schema.projects.status, "failed"))
    .all()
    .map((r) => r.id);

  if (failedIds.length === 0) {
    return { ok: true, message: "No failed projects to delete." };
  }

  let deleted = 0;
  let jobsCancelled = 0;
  let filesDeleted = 0;
  for (const id of failedIds) {
    const r = deleteProjectPermanently(id);
    if (r.ok) deleted += 1;
    jobsCancelled += r.jobsCancelled ?? 0;
    filesDeleted += r.filesDeleted ?? 0;
  }

  revalidatePath("/admin");
  revalidatePath("/");
  revalidatePath("/storage");
  return {
    ok: true,
    message: `Deleted ${deleted} failed project(s) · ${jobsCancelled} job(s) cancelled · ${filesDeleted} file(s) removed.`,
    details: { deleted, jobsCancelled, filesDeleted },
  };
}

/** Delete a single project by id (jobs, media, and all DB rows). */
export async function deleteProjectAction(
  projectId: string,
): Promise<AdminActionResult> {
  const result = deleteProjectPermanently(projectId);
  revalidatePath("/admin");
  revalidatePath("/");
  revalidatePath("/storage");
  revalidatePath(`/projects/${projectId}`);
  return {
    ok: result.ok,
    message: result.message,
    details: {
      jobsCancelled: result.jobsCancelled ?? 0,
      filesDeleted: result.filesDeleted ?? 0,
    },
  };
}

/** Delete jobs that finished more than `olderThanMinutes` ago, in any final state. */
export async function pruneFinishedJobsAction(
  olderThanMinutes = 60,
): Promise<AdminActionResult> {
  const cutoff = Date.now() - olderThanMinutes * 60_000;
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
  revalidatePath("/admin");
  return {
    ok: true,
    message: `Pruned ${changes} finished job(s) older than ${olderThanMinutes} min.`,
    details: { changes },
  };
}
