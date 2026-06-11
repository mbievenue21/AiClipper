import Link from "next/link";
import { ArrowLeft, BarChart3 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  formatDurationMs,
  getAnalyticsOverview,
  stageMedians,
} from "@/lib/pipeline/analytics";
import { PIPELINE_STAGE_DEFS } from "@/lib/db/schema";
import { getProfileAnalyticsOverview } from "@/lib/profiles/queries";
import { cn } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function AnalyticsPage() {
  const rows = getAnalyticsOverview();
  const medians = stageMedians(rows);
  const profileAnalytics = await getProfileAnalyticsOverview();

  const globalBottleneck = (() => {
    let best: { key: string; label: string; total: number } | null = null;
    for (const def of PIPELINE_STAGE_DEFS) {
      const total = rows.reduce(
        (s, r) => s + (r.stages.find((x) => x.key === def.key)?.durationMs ?? 0),
        0,
      );
      if (!best || total > best.total) {
        best = { key: def.key, label: def.label, total };
      }
    }
    return best;
  })();

  return (
    <div className="container mx-auto max-w-6xl px-4 py-10">
      <div className="mb-6 flex items-center gap-3">
        <Button variant="ghost" size="sm" asChild>
          <Link href="/">
            <ArrowLeft className="size-4" />
            Projects
          </Link>
        </Button>
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <BarChart3 className="size-6" />
            Pipeline analytics
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Compare stage durations across projects to find bottlenecks and
            timeouts.
          </p>
        </div>
      </div>

      {rows.length === 0 ? (
        <Card className="border-dashed">
          <CardHeader>
            <CardTitle>No timing data yet</CardTitle>
            <CardDescription>
              Process a project through to highlights. New runs record per-stage
              timings automatically; older projects may show estimates from job
              timestamps.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <>
          <div className="mb-6 grid gap-4 sm:grid-cols-3">
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Projects tracked</CardDescription>
                <CardTitle className="text-2xl">{rows.length}</CardTitle>
              </CardHeader>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Slowest stage (aggregate)</CardDescription>
                <CardTitle className="text-lg">
                  {globalBottleneck?.label ?? "—"}
                </CardTitle>
              </CardHeader>
            </Card>
            <Card>
              <CardHeader className="pb-2">
                <CardDescription>Longest total pipeline</CardDescription>
                <CardTitle className="text-lg">
                  {formatDurationMs(rows[0]?.totalMs ?? 0)}
                </CardTitle>
              </CardHeader>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Stage comparison</CardTitle>
              <CardDescription>
                Median duration per stage across projects below. Cells show
                project time vs median (darker = slower than median).
              </CardDescription>
            </CardHeader>
            <CardContent className="overflow-x-auto">
              <table className="w-full min-w-[720px] border-collapse text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="sticky left-0 z-10 bg-card py-2 pr-4 font-medium">
                      Project
                    </th>
                    <th className="px-2 py-2 font-medium">Total</th>
                    <th className="px-2 py-2 font-medium">Bottleneck</th>
                    {PIPELINE_STAGE_DEFS.map((def) => (
                      <th
                        key={def.key}
                        className="px-2 py-2 font-medium whitespace-nowrap"
                        title={
                          medians[def.key]
                            ? `Median: ${formatDurationMs(medians[def.key]!)}`
                            : undefined
                        }
                      >
                        {def.label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr
                      key={row.projectId}
                      className="border-b border-border/60 hover:bg-muted/30"
                    >
                      <td className="sticky left-0 z-10 bg-card py-2 pr-4">
                        <Link
                          href={`/projects/${row.projectId}`}
                          className="font-medium hover:underline"
                        >
                          {row.projectName}
                        </Link>
                        <div className="text-[10px] text-muted-foreground">
                          {row.highlightCount} highlights
                          {row.fromBackfill ? " · estimated" : ""}
                        </div>
                      </td>
                      <td className="px-2 py-2 tabular-nums whitespace-nowrap">
                        {formatDurationMs(row.totalMs)}
                      </td>
                      <td className="px-2 py-2 whitespace-nowrap">
                        <Badge variant="secondary" className="text-[10px]">
                          {row.stages.find((s) => s.key === row.bottleneck)
                            ?.label ?? "—"}
                        </Badge>
                      </td>
                      {PIPELINE_STAGE_DEFS.map((def) => {
                        const stage = row.stages.find((s) => s.key === def.key);
                        const ms = stage?.durationMs ?? 0;
                        const median = medians[def.key];
                        const slow =
                          median != null && ms > 0 && ms > median * 1.25;
                        const isTimeout =
                          stage?.status === "timeout" ||
                          stage?.status === "failed";
                        if (ms === 0) {
                          return (
                            <td
                              key={def.key}
                              className="px-2 py-2 text-center text-muted-foreground"
                            >
                              —
                            </td>
                          );
                        }
                        return (
                          <td
                            key={def.key}
                            className={cn(
                              "px-2 py-2 text-center tabular-nums text-xs whitespace-nowrap",
                              slow && "font-medium text-amber-600 dark:text-amber-400",
                              isTimeout && "text-destructive font-medium",
                            )}
                          >
                            {formatDurationMs(ms)}
                            {isTimeout && (
                              <span className="ml-1 text-[9px] uppercase">
                                {stage?.status}
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                  <tr className="border-t-2 bg-muted/40 font-medium">
                    <td className="sticky left-0 z-10 bg-muted/40 py-2 pr-4 text-xs">
                      Median (all projects)
                    </td>
                    <td className="px-2 py-2 text-xs text-muted-foreground">—</td>
                    <td className="px-2 py-2 text-xs text-muted-foreground">—</td>
                    {PIPELINE_STAGE_DEFS.map((def) => {
                      const median = medians[def.key];
                      return (
                        <td
                          key={def.key}
                          className="px-2 py-2 text-center text-xs tabular-nums text-muted-foreground"
                        >
                          {median != null ? formatDurationMs(median) : "—"}
                        </td>
                      );
                    })}
                  </tr>
                </tbody>
              </table>
            </CardContent>
          </Card>

          <Card className="mt-6">
            <CardHeader>
              <CardTitle>Profile training metrics</CardTitle>
              <CardDescription>
                How highlight profiles are learning from reference clips and
                feedback.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="rounded-md border p-4">
                  <p className="text-xs text-muted-foreground">Profiles</p>
                  <p className="text-2xl font-semibold">
                    {profileAnalytics.profiles.length}
                  </p>
                </div>
                <div className="rounded-md border p-4">
                  <p className="text-xs text-muted-foreground">Training runs</p>
                  <p className="text-2xl font-semibold">
                    {profileAnalytics.runs.length}
                  </p>
                </div>
                <div className="rounded-md border p-4">
                  <p className="text-xs text-muted-foreground">Examples</p>
                  <p className="text-2xl font-semibold">
                    {profileAnalytics.examples.length}
                  </p>
                </div>
              </div>

              {profileAnalytics.profiles.length > 0 && (
                <div className="space-y-2">
                  {profileAnalytics.profiles.map((p) => {
                    const runs = profileAnalytics.runs.filter(
                      (r) => r.profileId === p.id,
                    );
                    const latest = runs[0];
                    return (
                      <div
                        key={p.id}
                        className="flex items-center justify-between rounded-md border p-3 text-sm"
                      >
                        <div>
                          <Link
                            href={`/profiles/${p.id}#training-board`}
                            className="font-medium hover:underline"
                          >
                            {p.name}
                          </Link>
                          <p className="text-xs text-muted-foreground">
                            {latest?.status ?? "no runs"} ·{" "}
                            {latest?.metricsJson?.trialCount ?? 0} trials
                          </p>
                        </div>
                        {latest?.metricsJson?.recallAtK != null && (
                          <Badge variant="secondary">
                            recall@K {(latest.metricsJson.recallAtK * 100).toFixed(0)}%
                          </Badge>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
