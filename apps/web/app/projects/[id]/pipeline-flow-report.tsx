"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, FileSearch } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { PipelineFlowReport } from "@/lib/db/schema";

const STAGE_LABELS: Record<string, string> = {
  ingest: "1. Ingest",
  transcribe: "2. Transcribe",
  twelvelabs_index: "3. TwelveLabs index",
  twelvelabs_analyze: "4. TwelveLabs analyze",
  analyze: "5. Local analyze + Gemini",
};

function statusBadge(status: unknown) {
  const s = String(status ?? "unknown");
  if (s === "ok") return <Badge variant="default">ok</Badge>;
  if (s === "skipped") return <Badge variant="secondary">skipped</Badge>;
  if (s === "failed") return <Badge variant="destructive">failed</Badge>;
  return <Badge variant="outline">{s}</Badge>;
}

function StageDetail({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(
    ([k]) => k !== "updatedAt" && k !== "status",
  );
  if (entries.length === 0) {
    return <p className="text-xs text-muted-foreground">No details recorded.</p>;
  }
  return (
    <dl className="mt-2 grid gap-1 text-xs">
      {entries.map(([key, value]) => (
        <div key={key} className="flex justify-between gap-3">
          <dt className="text-muted-foreground">{key}</dt>
          <dd className="max-w-[60%] truncate font-mono text-right text-foreground">
            {typeof value === "object"
              ? JSON.stringify(value)
              : String(value ?? "—")}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function PipelineFlowReportPanel({
  report,
  show,
}: {
  report: PipelineFlowReport | null | undefined;
  show: boolean;
}) {
  const [expanded, setExpanded] = useState(true);
  const [openStages, setOpenStages] = useState<Record<string, boolean>>({});

  if (!show || !report?.stages || Object.keys(report.stages).length === 0) {
    return null;
  }

  const decisions = report.decisions ?? [];
  const settings = report.projectSettings;

  return (
    <Card className="mb-4 border-dashed">
      <CardHeader className="pb-3">
        <button
          type="button"
          className="flex w-full items-start gap-2 text-left"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? (
            <ChevronDown className="mt-0.5 size-4 shrink-0" />
          ) : (
            <ChevronRight className="mt-0.5 size-4 shrink-0" />
          )}
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <FileSearch className="size-4" />
              Pipeline flow report
            </CardTitle>
            <CardDescription>
              Path taken for this project before rendering — use this to debug
              skipped steps, missing visual fusion, or weak vibe targeting.
            </CardDescription>
          </div>
        </button>
      </CardHeader>
      {expanded && (
        <CardContent className="space-y-4 text-sm">
          {settings && (
            <div className="rounded-md border bg-muted/20 p-3 text-xs">
              <p className="font-medium">Project settings used</p>
              <p className="mt-1 text-muted-foreground">
                topN={settings.topN} · clips {settings.minClipSeconds}–
                {settings.maxClipSeconds}s · model={settings.analyzeModel}
                {settings.vibe
                  ? ` · vibe="${String(settings.vibe).slice(0, 80)}"`
                  : " · no vibe"}
              </p>
            </div>
          )}

          {decisions.length > 0 && (
            <div>
              <p className="mb-2 text-xs font-medium text-muted-foreground">
                Path summary
              </p>
              <ul className="space-y-1.5 text-xs">
                {decisions.map((line) => (
                  <li
                    key={line}
                    className="rounded-md border bg-muted/30 px-2.5 py-1.5"
                  >
                    {line}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">
              Stage details
            </p>
            {Object.entries(report.stages).map(([stage, data]) => {
              const open = openStages[stage] ?? false;
              return (
                <div key={stage} className="rounded-md border p-2.5">
                  <button
                    type="button"
                    className="flex w-full items-center justify-between gap-2 text-left text-xs"
                    onClick={() =>
                      setOpenStages((s) => ({ ...s, [stage]: !open }))
                    }
                  >
                    <span className="font-medium">
                      {STAGE_LABELS[stage] ?? stage}
                    </span>
                    <span className="flex items-center gap-2">
                      {statusBadge(data.status)}
                      {open ? (
                        <ChevronDown className="size-3.5" />
                      ) : (
                        <ChevronRight className="size-3.5" />
                      )}
                    </span>
                  </button>
                  {open && <StageDetail data={data} />}
                </div>
              );
            })}
          </div>

          {report.complete && (
            <p className="text-[11px] text-muted-foreground">
              Report finalized after analyze — safe to optimize and re-run via
              Re-analyze.
            </p>
          )}
        </CardContent>
      )}
    </Card>
  );
}
