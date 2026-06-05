/**
 * /admin — Worker tools.
 *
 * Read-only health dashboard + buttons for safe DB cleanup. The worker
 * itself cannot be killed from here (we'd be killing the process serving
 * the page); for that, run `pnpm worker:reset` in a terminal.
 */
import { desc, sql } from "drizzle-orm";
import { formatDistanceToNow } from "date-fns";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  Server,
  Terminal,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { db, schema } from "@/lib/db/client";
import { fetchWorkerStats, pingWorker } from "@/lib/worker";
import { getTwelveLabsConfigStatus } from "@/lib/twelvelabs/status";

import { AdminActionsPanel } from "./admin-actions-panel";

export const dynamic = "force-dynamic";

const jobStatusVariants: Record<
  string,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "outline",
  running: "secondary",
  succeeded: "default",
  failed: "destructive",
  cancelled: "outline",
};

const projectStatusVariants: Record<
  string,
  "default" | "secondary" | "destructive" | "outline"
> = {
  pending: "secondary",
  ingesting: "secondary",
  transcribing: "secondary",
  analyzing: "secondary",
  ready: "default",
  failed: "destructive",
};

function formatAge(seconds: number | null) {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export default async function AdminPage() {
  const [health, stats] = await Promise.all([
    pingWorker(),
    fetchWorkerStats(),
  ]);

  // Always read DB-side stats too — they're authoritative if the worker is
  // down, and we want the page to render something useful in that case.
  const jobsByStatus = db
    .select({
      status: schema.jobs.status,
      count: sql<number>`COUNT(*)`,
    })
    .from(schema.jobs)
    .groupBy(schema.jobs.status)
    .all();

  const projectsByStatus = db
    .select({
      status: schema.projects.status,
      count: sql<number>`COUNT(*)`,
    })
    .from(schema.projects)
    .groupBy(schema.projects.status)
    .all();

  const recentJobs = db
    .select({
      id: schema.jobs.id,
      type: schema.jobs.type,
      status: schema.jobs.status,
      progressMessage: schema.jobs.progressMessage,
      projectId: schema.jobs.projectId,
      createdAt: schema.jobs.createdAt,
      startedAt: schema.jobs.startedAt,
      finishedAt: schema.jobs.finishedAt,
      errorMessage: schema.jobs.errorMessage,
      attempts: schema.jobs.attempts,
    })
    .from(schema.jobs)
    .orderBy(desc(schema.jobs.createdAt))
    .limit(20)
    .all();

  const stuckProjects = db
    .select({
      id: schema.projects.id,
      name: schema.projects.name,
      status: schema.projects.status,
      updatedAt: schema.projects.updatedAt,
    })
    .from(schema.projects)
    .where(
      sql`${schema.projects.status} IN ('pending','ingesting','transcribing','analyzing')`,
    )
    .orderBy(desc(schema.projects.updatedAt))
    .all();

  const totalJobs = jobsByStatus.reduce((s, r) => s + Number(r.count), 0);
  const totalProjects = projectsByStatus.reduce(
    (s, r) => s + Number(r.count),
    0,
  );

  const tlConfig = getTwelveLabsConfigStatus();

  const tlIndexByStatus = db
    .select({
      status: schema.externalVideoIndexes.status,
      count: sql<number>`COUNT(*)`,
    })
    .from(schema.externalVideoIndexes)
    .groupBy(schema.externalVideoIndexes.status)
    .all();

  const visualSegmentTotal = db
    .select({ count: sql<number>`COUNT(*)` })
    .from(schema.visualSegments)
    .all()[0]?.count ?? 0;

  const failedTlJobs = db
    .select({
      id: schema.jobs.id,
      type: schema.jobs.type,
      errorMessage: schema.jobs.errorMessage,
      createdAt: schema.jobs.createdAt,
    })
    .from(schema.jobs)
    .where(
      sql`${schema.jobs.status} = 'failed' AND ${schema.jobs.type} IN ('twelvelabs_index','twelvelabs_analyze')`,
    )
    .orderBy(desc(schema.jobs.createdAt))
    .limit(5)
    .all();

  const recentTlJobs = recentJobs.filter((j) =>
    j.type === "twelvelabs_index" || j.type === "twelvelabs_analyze",
  );

  const healthBody = health.body as
    | { twelvelabs?: Record<string, unknown> }
    | undefined;

  return (
    <div className="container mx-auto max-w-6xl space-y-6 px-4 py-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Worker tools
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Health dashboard and one-click cleanup for the background worker.
            If the worker is unreachable or completely jammed, run{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
              pnpm worker:reset
            </code>{" "}
            in a terminal to kill zombie processes and free port 8000.
          </p>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        {/* Worker health */}
        <Card>
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2 text-base">
                <Server className="size-4" />
                Worker
              </CardTitle>
              {health.ok ? (
                <Badge>
                  <CheckCircle2 className="mr-1 size-3" />
                  online
                </Badge>
              ) : (
                <Badge variant="destructive">
                  <XCircle className="mr-1 size-3" />
                  offline
                </Badge>
              )}
            </div>
            <CardDescription className="break-all text-xs">
              {health.url}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-xs text-muted-foreground">
            <div>
              <span className="text-foreground">Latency:</span>{" "}
              {health.latencyMs ?? "—"} ms
            </div>
            {health.status && (
              <div>
                <span className="text-foreground">HTTP:</span> {health.status}
              </div>
            )}
            {health.error && (
              <div className="text-destructive">{health.error}</div>
            )}
            {stats?.oldest_pending_age_s != null && (
              <div>
                <span className="text-foreground">Oldest pending:</span>{" "}
                {formatAge(stats.oldest_pending_age_s)}
              </div>
            )}
            {stats?.oldest_running_age_s != null && (
              <div>
                <span className="text-foreground">Oldest running:</span>{" "}
                {formatAge(stats.oldest_running_age_s)}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Jobs */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Jobs ({totalJobs})</CardTitle>
            <CardDescription className="text-xs">
              Counts by status (live DB)
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-1.5 text-xs">
            {jobsByStatus.length === 0 ? (
              <span className="text-muted-foreground">No jobs yet.</span>
            ) : (
              jobsByStatus.map((row) => (
                <Badge
                  key={row.status}
                  variant={jobStatusVariants[row.status] ?? "secondary"}
                >
                  {row.status}: {Number(row.count)}
                </Badge>
              ))
            )}
          </CardContent>
        </Card>

        {/* Projects */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">
              Projects ({totalProjects})
            </CardTitle>
            <CardDescription className="text-xs">
              Counts by status (live DB)
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-1.5 text-xs">
            {projectsByStatus.length === 0 ? (
              <span className="text-muted-foreground">No projects yet.</span>
            ) : (
              projectsByStatus.map((row) => (
                <Badge
                  key={row.status}
                  variant={projectStatusVariants[row.status] ?? "secondary"}
                >
                  {row.status}: {Number(row.count)}
                </Badge>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">TwelveLabs video analysis</CardTitle>
          <CardDescription className="text-xs">
            Global config and index health. Per-project visual segments and job
            logs live on each project page.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3 text-xs">
          <div className="flex flex-wrap gap-1.5">
            <Badge variant={tlConfig.enabled ? "default" : "secondary"}>
              {tlConfig.enabled ? "enabled" : "disabled"}
            </Badge>
            {tlConfig.enabled && (
              <>
                <Badge
                  variant={tlConfig.apiKeyConfigured ? "outline" : "destructive"}
                >
                  API key {tlConfig.apiKeyConfigured ? "ok" : "missing"}
                </Badge>
                <Badge
                  variant={tlConfig.indexIdConfigured ? "outline" : "destructive"}
                >
                  index id {tlConfig.indexIdConfigured ? "ok" : "missing"}
                </Badge>
                <Badge variant="outline">
                  fail-open {tlConfig.failOpen ? "on" : "off"}
                </Badge>
              </>
            )}
            {tlConfig.multimodalEnabled && (
              <Badge variant="outline">gemini multimodal</Badge>
            )}
          </div>
          <div className="grid gap-2 sm:grid-cols-3">
            <div className="rounded-md border bg-muted/30 p-2">
              <p className="text-muted-foreground">Visual segments (all)</p>
              <p className="text-lg font-semibold">{Number(visualSegmentTotal)}</p>
            </div>
            <div className="rounded-md border bg-muted/30 p-2">
              <p className="text-muted-foreground">Index rows</p>
              <div className="mt-1 flex flex-wrap gap-1">
                {tlIndexByStatus.length === 0 ? (
                  <span className="text-muted-foreground">none</span>
                ) : (
                  tlIndexByStatus.map((r) => (
                    <Badge key={r.status} variant="secondary">
                      {r.status}: {Number(r.count)}
                    </Badge>
                  ))
                )}
              </div>
            </div>
            <div className="rounded-md border bg-muted/30 p-2">
              <p className="text-muted-foreground">Worker /health</p>
              <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                {healthBody?.twelvelabs
                  ? JSON.stringify(healthBody.twelvelabs)
                  : health.ok
                    ? "no twelvelabs block (restart worker)"
                    : "worker offline"}
              </p>
            </div>
          </div>
          {failedTlJobs.length > 0 && (
            <div className="rounded-md border border-destructive/30 bg-destructive/5 p-2">
              <p className="font-medium text-destructive">Recent TwelveLabs failures</p>
              <ul className="mt-1 space-y-1 text-destructive/90">
                {failedTlJobs.map((j) => (
                  <li key={j.id}>
                    {j.type} · {formatDistanceToNow(j.createdAt, { addSuffix: true })}
                    {j.errorMessage ? ` — ${j.errorMessage.slice(0, 100)}` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {recentTlJobs.length > 0 && (
            <div>
              <p className="mb-1 font-medium text-muted-foreground">
                Recent TwelveLabs jobs
              </p>
              <ul className="space-y-1">
                {recentTlJobs.slice(0, 5).map((j) => (
                  <li key={j.id} className="flex justify-between gap-2">
                    <span className="font-mono">{j.type}</span>
                    <Badge variant={jobStatusVariants[j.status] ?? "secondary"}>
                      {j.status}
                    </Badge>
                    <span className="truncate text-muted-foreground">
                      {j.progressMessage ?? ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <AdminActionsPanel />

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Terminal className="size-4" />
              Hard reset (terminal)
            </CardTitle>
            <CardDescription>
              When the worker won&apos;t come back online (port 8000 stuck,
              uvicorn zombies, etc.) — these run outside the web app.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div>
              <p className="mb-1 font-medium">Full reset</p>
              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
                <code>pnpm worker:reset</code>
              </pre>
              <p className="mt-1 text-xs text-muted-foreground">
                Kills every <code>uvicorn worker.main</code> process, frees
                port 8000, resets stuck jobs, and heals orphan projects.
              </p>
            </div>
            <div>
              <p className="mb-1 font-medium">Preview only (dry run)</p>
              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
                <code>pnpm worker:reset:dry</code>
              </pre>
            </div>
            <Separator />
            <p className="text-xs text-muted-foreground">
              After a hard reset, start the stack again with{" "}
              <code className="rounded bg-muted px-1 py-0.5">pnpm dev</code>.
            </p>
          </CardContent>
        </Card>
      </div>

      {stuckProjects.length > 0 && (
        <Card className="border-amber-500/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <AlertTriangle className="size-4 text-amber-500" />
              Projects in flight ({stuckProjects.length})
            </CardTitle>
            <CardDescription>
              Currently mid-pipeline. Healthy if they have an active job; if
              not, run &quot;Heal stuck workers&quot; above.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {stuckProjects.map((p) => (
              <div
                key={p.id}
                className="flex items-center justify-between gap-3 rounded-md border p-2"
              >
                <div className="min-w-0">
                  <p className="truncate font-medium">{p.name}</p>
                  <p className="truncate text-xs text-muted-foreground">
                    {p.id} · updated{" "}
                    {formatDistanceToNow(p.updatedAt, { addSuffix: true })}
                  </p>
                </div>
                <Badge variant={projectStatusVariants[p.status] ?? "secondary"}>
                  {p.status}
                </Badge>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Clock className="size-4" />
            Recent jobs (20)
          </CardTitle>
          <CardDescription>
            Newest first. Use this to spot retries, slow steps, or repeated
            failures.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {recentJobs.length === 0 ? (
            <p className="text-sm text-muted-foreground">No jobs yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-muted-foreground">
                  <tr className="border-b">
                    <th className="px-2 py-2 text-left font-medium">Type</th>
                    <th className="px-2 py-2 text-left font-medium">Status</th>
                    <th className="px-2 py-2 text-left font-medium">
                      Progress
                    </th>
                    <th className="px-2 py-2 text-left font-medium">
                      Attempts
                    </th>
                    <th className="px-2 py-2 text-left font-medium">Project</th>
                    <th className="px-2 py-2 text-left font-medium">Created</th>
                    <th className="px-2 py-2 text-left font-medium">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {recentJobs.map((j) => (
                    <tr key={j.id} className="border-b last:border-b-0">
                      <td className="px-2 py-2 font-mono">{j.type}</td>
                      <td className="px-2 py-2">
                        <Badge
                          variant={
                            jobStatusVariants[j.status] ?? "secondary"
                          }
                        >
                          {j.status}
                        </Badge>
                      </td>
                      <td className="px-2 py-2 text-muted-foreground">
                        {j.progressMessage ?? "—"}
                      </td>
                      <td className="px-2 py-2 text-muted-foreground">
                        {j.attempts}
                      </td>
                      <td className="px-2 py-2 font-mono text-muted-foreground">
                        {j.projectId ?? "—"}
                      </td>
                      <td className="px-2 py-2 text-muted-foreground">
                        {formatDistanceToNow(j.createdAt, { addSuffix: true })}
                      </td>
                      <td
                        className="max-w-[24ch] truncate px-2 py-2 text-destructive"
                        title={j.errorMessage ?? ""}
                      >
                        {j.errorMessage ?? ""}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
