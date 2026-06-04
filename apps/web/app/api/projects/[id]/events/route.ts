/**
 * GET /api/projects/<id>/events
 *
 * Server-Sent Events stream that pushes a project snapshot whenever any of
 * its jobs / clips / scheduled uploads changes. Replaces the polling
 * fallback (RefreshWhenRunning) for live progress updates.
 *
 * Implementation: we poll the SQLite DB every 750ms from inside the route
 * (cheap because of the existing indexes) and ONLY emit a new SSE event
 * when the snapshot hash differs from the last one we sent. That means
 * idle projects produce essentially zero network traffic.
 *
 * Lifecycle: the stream stays open until the client disconnects (closing
 * the EventSource on the React side) or 15 minutes have elapsed — at which
 * point we close so an abandoned tab doesn't keep a connection forever.
 */
import { asc, desc, eq, inArray } from "drizzle-orm";
import { NextRequest } from "next/server";

import { db, schema } from "@/lib/db/client";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const POLL_INTERVAL_MS = 750;
const MAX_LIFETIME_MS = 15 * 60 * 1000;

type Snapshot = {
  project: {
    id: string;
    status: string;
    notes: string | null;
    updatedAt: number;
  };
  jobs: {
    id: string;
    type: string;
    status: string;
    progress: number;
    progressMessage: string | null;
    errorMessage: string | null;
    payloadJson: Record<string, unknown>;
  }[];
  clips: {
    id: string;
    highlightId: string;
    status: string;
    hasCaptions: boolean;
    captionedFilePath: string | null;
    filePath: string;
    dominantColor: string | null;
    updatedAt: number;
  }[];
  uploads: {
    id: string;
    clipId: string;
    platform: string;
    status: string;
    externalUrl: string | null;
    errorMessage: string | null;
    scheduledFor: number;
  }[];
};

async function readSnapshot(projectId: string): Promise<Snapshot | null> {
  const [project] = db
    .select({
      id: schema.projects.id,
      status: schema.projects.status,
      notes: schema.projects.notes,
      updatedAt: schema.projects.updatedAt,
    })
    .from(schema.projects)
    .where(eq(schema.projects.id, projectId))
    .all();
  if (!project) return null;

  const jobs = db
    .select({
      id: schema.jobs.id,
      type: schema.jobs.type,
      status: schema.jobs.status,
      progress: schema.jobs.progress,
      progressMessage: schema.jobs.progressMessage,
      errorMessage: schema.jobs.errorMessage,
      payloadJson: schema.jobs.payloadJson,
    })
    .from(schema.jobs)
    .where(eq(schema.jobs.projectId, projectId))
    .orderBy(desc(schema.jobs.createdAt))
    .limit(50)
    .all();

  const highlightIds = db
    .select({ id: schema.highlights.id })
    .from(schema.highlights)
    .innerJoin(schema.videos, eq(schema.highlights.videoId, schema.videos.id))
    .where(eq(schema.videos.projectId, projectId))
    .all()
    .map((r) => r.id);

  const clips =
    highlightIds.length > 0
      ? db
          .select({
            id: schema.clips.id,
            highlightId: schema.clips.highlightId,
            status: schema.clips.status,
            hasCaptions: schema.clips.hasCaptions,
            captionedFilePath: schema.clips.captionedFilePath,
            filePath: schema.clips.filePath,
            dominantColor: schema.clips.dominantColor,
            updatedAt: schema.clips.updatedAt,
          })
          .from(schema.clips)
          .where(inArray(schema.clips.highlightId, highlightIds))
          .orderBy(desc(schema.clips.createdAt))
          .all()
      : [];

  const clipIds = clips.map((c) => c.id);
  const uploads =
    clipIds.length > 0
      ? db
          .select({
            id: schema.scheduledUploads.id,
            clipId: schema.scheduledUploads.clipId,
            platform: schema.scheduledUploads.platform,
            status: schema.scheduledUploads.status,
            externalUrl: schema.scheduledUploads.externalUrl,
            errorMessage: schema.scheduledUploads.errorMessage,
            scheduledFor: schema.scheduledUploads.scheduledFor,
          })
          .from(schema.scheduledUploads)
          .where(inArray(schema.scheduledUploads.clipId, clipIds))
          .orderBy(asc(schema.scheduledUploads.scheduledFor))
          .all()
      : [];

  const toMs = (d: Date | number) =>
    d instanceof Date ? d.getTime() : Number(d);

  return {
    project: {
      id: project.id,
      status: project.status,
      notes: project.notes,
      updatedAt: toMs(project.updatedAt),
    },
    jobs: jobs.map((j) => ({
      id: j.id,
      type: j.type,
      status: j.status,
      progress: j.progress,
      progressMessage: j.progressMessage,
      errorMessage: j.errorMessage,
      payloadJson: (j.payloadJson as Record<string, unknown>) ?? {},
    })),
    clips: clips.map((c) => ({
      id: c.id,
      highlightId: c.highlightId,
      status: c.status,
      hasCaptions: c.hasCaptions,
      captionedFilePath: c.captionedFilePath,
      filePath: c.filePath,
      dominantColor: c.dominantColor,
      updatedAt: toMs(c.updatedAt),
    })),
    uploads: uploads.map((u) => ({
      id: u.id,
      clipId: u.clipId,
      platform: u.platform,
      status: u.status,
      externalUrl: u.externalUrl,
      errorMessage: u.errorMessage,
      scheduledFor: toMs(u.scheduledFor),
    })),
  };
}

function digest(s: Snapshot): string {
  // Cheap, stable digest — JSON.stringify is fine at this size and the
  // route is per-project, so collisions don't matter.
  return JSON.stringify(s);
}

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ id: string }> },
) {
  const { id } = await ctx.params;

  const encoder = new TextEncoder();
  let cancelled = false;

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const startedAt = Date.now();
      let lastDigest = "";

      const send = (event: string, data: unknown) => {
        if (cancelled) return;
        const payload =
          `event: ${event}\n` +
          `data: ${JSON.stringify(data)}\n\n`;
        controller.enqueue(encoder.encode(payload));
      };

      // Send a keepalive comment every 20s so proxies don't close us.
      const ka = setInterval(() => {
        if (cancelled) return;
        try {
          controller.enqueue(encoder.encode(`: ka ${Date.now()}\n\n`));
        } catch {
          /* stream already closed */
        }
      }, 20_000);

      // Initial snapshot.
      const first = await readSnapshot(id);
      if (first === null) {
        send("error", { message: "project not found" });
        clearInterval(ka);
        try {
          controller.close();
        } catch {}
        return;
      }
      lastDigest = digest(first);
      send("snapshot", first);

      // Poll loop.
      while (!cancelled) {
        await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
        if (cancelled) break;
        if (Date.now() - startedAt > MAX_LIFETIME_MS) {
          send("close", { reason: "max_lifetime" });
          break;
        }
        try {
          const snap = await readSnapshot(id);
          if (snap === null) {
            send("close", { reason: "project_gone" });
            break;
          }
          const d = digest(snap);
          if (d !== lastDigest) {
            lastDigest = d;
            send("snapshot", snap);
          }
        } catch (err) {
          send("error", {
            message: err instanceof Error ? err.message : String(err),
          });
        }
      }

      clearInterval(ka);
      try {
        controller.close();
      } catch {
        /* already closed */
      }
    },
    cancel() {
      cancelled = true;
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-store, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
