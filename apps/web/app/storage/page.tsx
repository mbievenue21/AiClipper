/**
 * /storage — Disk + database + browser cleanup dashboard.
 *
 * Companion to /admin (which manages the worker queue). This page is for
 * keeping the app *light* — reclaiming disk space, trimming DB cruft, and
 * blowing away stale browser state.
 */
import {
  AlertTriangle,
  Database,
  FolderOpen,
  HardDrive,
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
import {
  formatBytes,
  scanStorage,
  type CategoryStat,
  type ProjectStorage,
} from "@/lib/storage-scan";

import { BrowserCleanup } from "./browser-cleanup";
import { OrphanList } from "./orphan-list";
import { StorageActionsPanel } from "./storage-actions-panel";

export const dynamic = "force-dynamic";

const PROJECT_STATUS_VARIANT: Record<
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

export default async function StoragePage() {
  const report = scanStorage();

  // The next-cache row gets its own card because it lives outside MEDIA_ROOT.
  const mediaCategories = report.categories.filter(
    (c) => c.category !== "next_cache",
  );
  const nextCache = report.categories.find((c) => c.category === "next_cache");

  const totalDiskBytes =
    report.totalBytes + (nextCache?.bytes ?? 0) + (report.dbBytes ?? 0);

  const projectsWithMedia = report.perProject.filter((p) => p.hasMedia);
  const orphanCount = mediaCategories.find((c) => c.category === "orphan")
    ?.files ?? 0;

  return (
    <div className="container mx-auto max-w-6xl space-y-6 px-4 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Storage &amp; cleanup
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Reclaim disk space, trim DB rows, and clear browser-side state.
          Pairs with{" "}
          <a className="underline" href="/admin">
            /admin
          </a>{" "}
          which handles the worker queue. Everything here is{" "}
          <strong>safe</strong> to read; destructive actions are labeled.
        </p>
      </div>

      {/* Top stats grid */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <FolderOpen className="size-4" />
              Media root
            </CardTitle>
            <CardDescription className="break-all text-xs">
              <code>{report.mediaRoot}</code>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-xs">
            <p>
              <span className="text-muted-foreground">Files:</span>{" "}
              {report.totalFiles.toLocaleString()}
            </p>
            <p>
              <span className="text-muted-foreground">Bytes:</span>{" "}
              <span className="font-medium">{formatBytes(report.totalBytes)}</span>
            </p>
            <p>
              <span className="text-muted-foreground">Projects with media:</span>{" "}
              {projectsWithMedia.length}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Database className="size-4" />
              SQLite database
            </CardTitle>
            <CardDescription className="text-xs">
              <code>data/app.db</code>
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-xs">
            <p>
              <span className="text-muted-foreground">Size:</span>{" "}
              <span className="font-medium">{formatBytes(report.dbBytes)}</span>
            </p>
            <p className="text-muted-foreground">
              Use <Badge variant="outline">Compact SQLite</Badge> after
              deleting projects/jobs to return space to the OS.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <HardDrive className="size-4" />
              Combined footprint
            </CardTitle>
            <CardDescription className="text-xs">
              Media + DB + Next.js cache
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-1 text-xs">
            <p>
              <span className="text-muted-foreground">Media:</span>{" "}
              {formatBytes(report.totalBytes)}
            </p>
            <p>
              <span className="text-muted-foreground">Next cache:</span>{" "}
              {formatBytes(nextCache?.bytes ?? 0)}{" "}
              <span className="text-muted-foreground">
                ({nextCache?.files ?? 0} files)
              </span>
            </p>
            <Separator className="my-1" />
            <p>
              <span className="text-muted-foreground">Total on disk:</span>{" "}
              <span className="font-semibold">
                {formatBytes(totalDiskBytes)}
              </span>
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Category breakdown */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">By category</CardTitle>
          <CardDescription>
            Where the bytes actually live. Click a cleanup action below to
            target a row.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <CategoryTable
            categories={mediaCategories}
            totalBytes={report.totalBytes}
          />
        </CardContent>
      </Card>

      {/* Action panels */}
      <StorageActionsPanel />

      {/* Per-project breakdown */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">By project</CardTitle>
          <CardDescription>
            Largest first. Delete an individual project from{" "}
            <a className="underline" href="/admin">
              /admin
            </a>{" "}
            (we don&apos;t expose single-project delete here to keep the
            panel focused on bulk operations).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ProjectTable rows={report.perProject} totalBytes={report.totalBytes} />
        </CardContent>
      </Card>

      {/* Orphan files */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            {orphanCount > 0 && (
              <AlertTriangle className="size-4 text-amber-500" />
            )}
            Orphan files ({orphanCount})
          </CardTitle>
          <CardDescription>
            Files on disk that no DB row references. Usually leftovers from
            failed renders or aborted jobs. Subtitle files (.ass/.srt/.vtt)
            are excluded from this list — they live alongside clips.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <OrphanList
            orphans={report.orphans.map((o) => ({
              relPath: o.relPath,
              bytes: o.bytes,
              mtimeMs: o.mtimeMs,
            }))}
          />
        </CardContent>
      </Card>

      {/* Browser cleanup (client) */}
      <BrowserCleanup />
    </div>
  );
}

function CategoryTable({
  categories,
  totalBytes,
}: {
  categories: CategoryStat[];
  totalBytes: number;
}) {
  const sorted = [...categories].sort((a, b) => b.bytes - a.bytes);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-muted-foreground">
          <tr className="border-b">
            <th className="px-2 py-2 text-left font-medium">Category</th>
            <th className="px-2 py-2 text-right font-medium">Files</th>
            <th className="px-2 py-2 text-right font-medium">Size</th>
            <th className="px-2 py-2 text-left font-medium" style={{ width: "30%" }}>
              Share
            </th>
            <th className="px-2 py-2 text-left font-medium">Notes</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => {
            const pct =
              totalBytes > 0 ? (c.bytes / totalBytes) * 100 : 0;
            return (
              <tr key={c.category} className="border-b last:border-b-0 align-top">
                <td className="px-2 py-2 font-medium">{c.label}</td>
                <td className="px-2 py-2 text-right">
                  {c.files.toLocaleString()}
                </td>
                <td className="px-2 py-2 text-right">{formatBytes(c.bytes)}</td>
                <td className="px-2 py-2">
                  <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full bg-foreground/70"
                      style={{ width: `${pct.toFixed(1)}%` }}
                    />
                  </div>
                  <p className="mt-0.5 text-[10px] text-muted-foreground">
                    {pct.toFixed(1)}%
                  </p>
                </td>
                <td className="px-2 py-2 text-muted-foreground">
                  {c.description}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ProjectTable({
  rows,
  totalBytes,
}: {
  rows: ProjectStorage[];
  totalBytes: number;
}) {
  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">No projects yet.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-muted-foreground">
          <tr className="border-b">
            <th className="px-2 py-2 text-left font-medium">Project</th>
            <th className="px-2 py-2 text-left font-medium">Status</th>
            <th className="px-2 py-2 text-right font-medium">Files</th>
            <th className="px-2 py-2 text-right font-medium">Size</th>
            <th className="px-2 py-2 text-left font-medium" style={{ width: "30%" }}>
              Share
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => {
            const pct = totalBytes > 0 ? (p.bytes / totalBytes) * 100 : 0;
            return (
              <tr key={p.projectId} className="border-b last:border-b-0">
                <td className="px-2 py-2 align-top">
                  <p className="font-medium">
                    {p.projectName ?? (
                      <span className="text-muted-foreground italic">
                        (no DB row)
                      </span>
                    )}
                  </p>
                  <p className="font-mono text-[10px] text-muted-foreground">
                    {p.projectId}
                  </p>
                </td>
                <td className="px-2 py-2 align-top">
                  <Badge
                    variant={
                      PROJECT_STATUS_VARIANT[p.status] ?? "secondary"
                    }
                  >
                    {p.status}
                  </Badge>
                </td>
                <td className="px-2 py-2 text-right">{p.files}</td>
                <td className="px-2 py-2 text-right">
                  {p.bytes > 0 ? formatBytes(p.bytes) : "—"}
                </td>
                <td className="px-2 py-2">
                  <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full bg-foreground/70"
                      style={{ width: `${pct.toFixed(1)}%` }}
                    />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
