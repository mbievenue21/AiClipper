import { Check, Loader2 } from "lucide-react";

import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

const JOB_LABELS: Record<string, string> = {
  ingest: "Downloading video",
  transcribe: "Transcribing audio",
  twelvelabs_index: "TwelveLabs — indexing video",
  twelvelabs_analyze: "TwelveLabs — visual analysis",
  analyze: "Fusion + Gemini rerank",
  render: "Rendering clip",
  caption: "Burning captions",
  reedit: "Saving clip edits",
  publish: "Publishing",
};

type JobRow = {
  id: string;
  type: string;
  status: string;
  progress: number;
  progressMessage: string | null;
  errorMessage: string | null;
  clipLabel?: string;
};

type StageState = "done" | "active" | "upcoming";

const BASE_STAGES = [
  { id: "ingest", label: "Ingest" },
  { id: "transcribe", label: "Transcribe" },
] as const;

const TL_STAGES = [
  { id: "tl_index", label: "TL Index" },
  { id: "tl_analyze", label: "TL Visual" },
] as const;

const TAIL_STAGES = [
  { id: "analyze", label: "Analyze" },
  { id: "highlights", label: "Highlights" },
  { id: "render", label: "Render" },
] as const;

type StageId =
  | (typeof BASE_STAGES)[number]["id"]
  | (typeof TL_STAGES)[number]["id"]
  | (typeof TAIL_STAGES)[number]["id"];

function stageStates(input: {
  projectStatus: string;
  hasVideo: boolean;
  hasTranscript: boolean;
  highlightCount: number;
  clipCount: number;
  activeJobTypes: Set<string>;
  twelvelabsEnabled: boolean;
  twelvelabsIndexReady: boolean;
  hasVisualSegments: boolean;
}): Record<StageId, StageState> {
  const {
    projectStatus,
    hasVideo,
    hasTranscript,
    highlightCount,
    clipCount,
    activeJobTypes,
    twelvelabsEnabled,
    twelvelabsIndexReady,
    hasVisualSegments,
  } = input;

  const ingest: StageState =
    projectStatus === "ingesting" || activeJobTypes.has("ingest")
      ? "active"
      : hasVideo
        ? "done"
        : "upcoming";

  const transcribe: StageState =
    projectStatus === "transcribing" || activeJobTypes.has("transcribe")
      ? "active"
      : hasTranscript
        ? "done"
        : ingest === "done"
          ? "upcoming"
          : "upcoming";

  const tl_index: StageState = !twelvelabsEnabled
    ? "upcoming"
    : activeJobTypes.has("twelvelabs_index")
      ? "active"
      : twelvelabsIndexReady || hasVisualSegments
        ? "done"
        : transcribe === "done"
          ? "upcoming"
          : "upcoming";

  const tl_analyze: StageState = !twelvelabsEnabled
    ? "upcoming"
    : activeJobTypes.has("twelvelabs_analyze")
      ? "active"
      : hasVisualSegments
        ? "done"
        : tl_index === "done"
          ? "upcoming"
          : "upcoming";

  const analyze: StageState =
    projectStatus === "analyzing" || activeJobTypes.has("analyze")
      ? "active"
      : highlightCount > 0 || (projectStatus === "ready" && hasTranscript)
        ? "done"
        : twelvelabsEnabled
          ? tl_analyze === "done" || tl_index === "done"
            ? "upcoming"
            : transcribe === "done"
              ? "upcoming"
              : "upcoming"
          : transcribe === "done"
            ? "upcoming"
            : "upcoming";

  const highlights: StageState =
    highlightCount > 0
      ? "done"
      : analyze === "done" && projectStatus === "ready"
        ? "done"
        : analyze === "active"
          ? "active"
          : "upcoming";

  const render: StageState =
    activeJobTypes.has("render") || activeJobTypes.has("caption")
      ? "active"
      : clipCount > 0
        ? "done"
        : highlights === "done"
          ? "upcoming"
          : "upcoming";

  return { ingest, transcribe, tl_index, tl_analyze, analyze, highlights, render };
}

export function ProjectPipelinePanel({
  projectStatus,
  hasVideo,
  hasTranscript,
  highlightCount,
  clipCount,
  activeJobs,
  show,
  twelvelabsEnabled = false,
  twelvelabsIndexReady = false,
  hasVisualSegments = false,
}: {
  projectStatus: string;
  hasVideo: boolean;
  hasTranscript: boolean;
  highlightCount: number;
  clipCount: number;
  activeJobs: JobRow[];
  show: boolean;
  twelvelabsEnabled?: boolean;
  twelvelabsIndexReady?: boolean;
  hasVisualSegments?: boolean;
}) {
  if (!show) return null;

  const activeJobTypes = new Set(activeJobs.map((j) => j.type));
  const states = stageStates({
    projectStatus,
    hasVideo,
    hasTranscript,
    highlightCount,
    clipCount,
    activeJobTypes,
    twelvelabsEnabled,
    twelvelabsIndexReady,
    hasVisualSegments,
  });

  const stages = [
    ...BASE_STAGES,
    ...(twelvelabsEnabled ? TL_STAGES : []),
    ...TAIL_STAGES,
  ];

  return (
    <div className="sticky top-0 z-20 -mx-4 mb-4 border-b border-border/60 bg-background/90 px-4 py-3 backdrop-blur-md supports-[backdrop-filter]:bg-background/75">
      <Card className="border-primary/20 shadow-sm transition-shadow duration-300">
        <CardContent className="space-y-3 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm font-medium">Pipeline</p>
            {activeJobs.length > 0 && (
              <p className="text-xs text-muted-foreground animate-pulse">
                Live — updates automatically
              </p>
            )}
          </div>

          <ol className="flex flex-wrap items-center gap-1.5 sm:gap-2">
            {stages.map((stage, idx) => {
              const state = states[stage.id];
              return (
                <li key={stage.id} className="flex items-center gap-1.5 sm:gap-2">
                  <StagePill label={stage.label} state={state} />
                  {idx < stages.length - 1 && (
                    <span
                      className={cn(
                        "hidden h-px w-3 sm:block sm:w-5",
                        state === "done" ? "bg-primary/40" : "bg-border",
                      )}
                      aria-hidden
                    />
                  )}
                </li>
              );
            })}
          </ol>

          {activeJobs.length > 0 ? (
            <div className="space-y-2.5 animate-in fade-in slide-in-from-top-1 duration-300">
              {activeJobs.map((job) => (
                <div key={job.id} className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="font-medium">
                      {JOB_LABELS[job.type] ?? job.type}
                      {job.clipLabel ? ` · ${job.clipLabel}` : null}
                    </span>
                    <span className="text-muted-foreground tabular-nums">
                      {Math.round(job.progress * 100)}%
                    </span>
                  </div>
                  <Progress
                    value={Math.round(job.progress * 100)}
                    className="h-1.5 transition-all duration-500"
                  />
                  {job.progressMessage && (
                    <p className="text-[11px] text-muted-foreground">
                      {job.progressMessage}
                    </p>
                  )}
                  {job.errorMessage && (
                    <pre className="max-h-24 overflow-auto rounded-md bg-destructive/10 p-2 text-[10px] text-destructive whitespace-pre-wrap">
                      {job.errorMessage}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              {highlightCount > 0
                ? `${highlightCount} highlight${highlightCount === 1 ? "" : "s"} ready — scroll below to render.`
                : projectStatus === "ready"
                  ? "Pipeline idle."
                  : "Waiting for the worker…"}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function StagePill({ label, state }: { label: string; state: StageState }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide transition-colors duration-300 sm:text-[11px]",
        state === "done" &&
          "border-primary/30 bg-primary/10 text-primary",
        state === "active" &&
          "border-primary bg-primary/15 text-primary shadow-sm",
        state === "upcoming" &&
          "border-border bg-muted/40 text-muted-foreground",
      )}
    >
      {state === "done" && <Check className="size-3" />}
      {state === "active" && <Loader2 className="size-3 animate-spin" />}
      {label}
    </span>
  );
}
