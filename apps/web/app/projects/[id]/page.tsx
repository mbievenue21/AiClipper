import Link from "next/link";
import { notFound } from "next/navigation";
import { and, asc, desc, eq, inArray, sql } from "drizzle-orm";
import { formatDistanceToNow } from "date-fns";
import { ArrowLeft, FileText, FileVideo, Film, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { db, schema } from "@/lib/db/client";
import {
  DEFAULT_CAPTION_SETTINGS,
  DEFAULT_PROJECT_SETTINGS,
  normalizeAnalyzeModel,
  type ClipCaptionSettings,
  type GeneratedUploadMetadata,
  type HighlightReason,
  type ProjectSettings,
} from "@/lib/db/schema";

import { ClipCard, type ClipCardData } from "./clip-card";
import { RenderHighlightButton } from "./highlight-actions";
import { ProjectLiveProgress } from "./project-live-progress";
import { ProjectPipelinePanel } from "./project-pipeline-panel";
import type { AccountOption } from "./schedule-upload-dialog";
import { TranscriptUploadDialog } from "./transcript-upload-dialog";
import { AnalysisBadges } from "./analysis-badges";
import { ProjectAnalysisDashboard } from "./analysis-dashboard";
import { HighlightEvidenceDetails } from "./highlight-evidence";
import { getTwelveLabsConfigStatus } from "@/lib/twelvelabs/status";
import { ReanalyzeDialog } from "./reanalyze-dialog";
import { DeleteProjectDialog } from "./delete-project-dialog";
import { PipelineTimingDialog } from "./pipeline-timing-dialog";
import { getProjectTimingBreakdown } from "@/lib/pipeline/analytics";

export const dynamic = "force-dynamic";

const statusVariants: Record<
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

function formatBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatTimecode(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

type PageProps = { params: Promise<{ id: string }> };

export default async function ProjectPage({ params }: PageProps) {
  const { id } = await params;

  const [project] = await db
    .select()
    .from(schema.projects)
    .where(eq(schema.projects.id, id))
    .limit(1);

  if (!project) notFound();

  const settings: ProjectSettings =
    project.settingsJson ?? DEFAULT_PROJECT_SETTINGS;

  const videos = await db
    .select()
    .from(schema.videos)
    .where(eq(schema.videos.projectId, id));

  const jobs = await db
    .select()
    .from(schema.jobs)
    .where(eq(schema.jobs.projectId, id))
    .orderBy(desc(schema.jobs.createdAt))
    .limit(30);

  const video = videos[0];

  const transcript = video
    ? (
        await db
          .select({
            id: schema.transcripts.id,
            videoId: schema.transcripts.videoId,
            language: schema.transcripts.language,
            model: schema.transcripts.model,
            fullText: schema.transcripts.fullText,
            createdAt: schema.transcripts.createdAt,
            segmentCount: sql<number>`
              (SELECT COUNT(*) FROM transcript_segments
               WHERE transcript_segments.transcript_id = transcripts.id)
            `.as("segment_count"),
          })
          .from(schema.transcripts)
          .where(eq(schema.transcripts.videoId, video.id))
          .limit(1)
      )[0] ?? null
    : null;

  const highlights = video
    ? await db
        .select()
        .from(schema.highlights)
        .where(eq(schema.highlights.videoId, video.id))
        .orderBy(desc(schema.highlights.score), asc(schema.highlights.startSeconds))
    : [];

  const highlightIds = highlights.map((h) => h.id);
  const clips =
    highlightIds.length > 0
      ? await db
          .select()
          .from(schema.clips)
          .where(inArray(schema.clips.highlightId, highlightIds))
          .orderBy(desc(schema.clips.createdAt))
      : [];

  const clipIds = clips.map((c) => c.id);
  const uploads =
    clipIds.length > 0
      ? await db
          .select()
          .from(schema.scheduledUploads)
          .where(inArray(schema.scheduledUploads.clipId, clipIds))
          .orderBy(asc(schema.scheduledUploads.scheduledFor))
      : [];

  const accounts = await db
    .select({
      id: schema.accounts.id,
      platform: schema.accounts.platform,
      label: schema.accounts.label,
    })
    .from(schema.accounts);

  // Index latest active job per (clip_id) so the clip card can show progress.
  const activeJobByClip = new Map<
    string,
    { type: string; progress: number; progressMessage: string | null }
  >();
  for (const j of jobs) {
    if (j.status !== "running" && j.status !== "pending") continue;
    if (j.type !== "render" && j.type !== "caption" && j.type !== "reedit")
      continue;
    const payload = j.payloadJson as {
      clip_id?: string;
      highlight_id?: string;
      parent_clip_id?: string;
    };
    let clipId = payload?.clip_id ?? payload?.parent_clip_id;
    if (!clipId && payload?.highlight_id) {
      const c = clips.find((x) => x.highlightId === payload.highlight_id);
      clipId = c?.id;
    }
    if (clipId && !activeJobByClip.has(clipId)) {
      activeJobByClip.set(clipId, {
        type: j.type,
        progress: j.progress,
        progressMessage: j.progressMessage,
      });
    }
  }

  const clipsByHighlight = new Map<string, typeof clips>();
  for (const c of clips) {
    const list = clipsByHighlight.get(c.highlightId) ?? [];
    list.push(c);
    clipsByHighlight.set(c.highlightId, list);
  }

  const clipCards: ClipCardData[] = [];
  for (const [highlightId, group] of clipsByHighlight) {
    const sorted = [...group].sort((a, b) => {
      const ta =
        a.createdAt instanceof Date ? a.createdAt.getTime() : Number(a.createdAt);
      const tb =
        b.createdAt instanceof Date ? b.createdAt.getTime() : Number(b.createdAt);
      return tb - ta;
    });
    const primary =
      sorted.find((c) => c.isActive !== false && !c.supersededAt) ?? sorted[0];
    if (!primary) continue;

    const highlight = highlights.find((h) => h.id === highlightId);
    const versions = sorted.map((v) => ({
      id: v.id,
      createdAt:
        v.createdAt instanceof Date
          ? v.createdAt.getTime()
          : Number(v.createdAt),
      durationSeconds: v.durationSeconds,
      hasCaptions: v.hasCaptions,
      isActive: v.isActive !== false && !v.supersededAt,
      versionLabel: v.versionLabel,
    }));

    const clipUploads = uploads
      .filter((u) => u.clipId === primary.id)
      .map((u) => ({
        id: u.id,
        platform: u.platform as "youtube" | "instagram",
        status: u.status,
        scheduledFor:
          u.scheduledFor instanceof Date
            ? u.scheduledFor.getTime()
            : Number(u.scheduledFor),
        timezone: u.timezone,
        externalUrl: u.externalUrl,
        errorMessage: u.errorMessage,
      }));

    clipCards.push({
      id: primary.id,
      highlightId: primary.highlightId,
      title:
        highlight?.title ||
        (highlight ? `Highlight @ ${formatTimecode(highlight.startSeconds)}` : "Clip"),
      status: primary.status as ClipCardData["status"],
      filePath: primary.filePath,
      captionedFilePath: primary.captionedFilePath,
      hasCaptions: primary.hasCaptions,
      aspect: primary.aspect,
      durationSeconds: primary.durationSeconds,
      dominantColor: primary.dominantColor,
      errorMessage: primary.errorMessage,
      captionStyle: (primary.captionStyleJson as ClipCaptionSettings) ?? null,
      generatedMetadata:
        (highlight?.generatedMetadataJson as GeneratedUploadMetadata) ?? null,
      createdAt:
        primary.createdAt instanceof Date
          ? primary.createdAt.getTime()
          : Number(primary.createdAt),
      versionLabel: primary.versionLabel,
      versions,
      activeJob: activeJobByClip.get(primary.id) ?? null,
      uploads: clipUploads,
    });
  }

  clipCards.sort((a, b) => b.createdAt - a.createdAt);

  const anyJobActive = jobs.some(
    (j) => j.status === "pending" || j.status === "running",
  );
  const pipelineActive =
    anyJobActive ||
    project.status === "ingesting" ||
    project.status === "transcribing" ||
    project.status === "analyzing";

  const statusLabel = (() => {
    if (project.status === "ready" && clips.length > 0) return "clips ready";
    if (project.status === "ready" && highlights.length > 0) return "highlights ready";
    if (project.status === "pending" && transcript) return "transcribed";
    if (project.status === "pending" && video) return "downloaded";
    if (project.status === "ready" && transcript) return "transcribed";
    return project.status;
  })();

  const accountOptions: AccountOption[] = accounts
    .filter((a) => a.platform === "youtube" || a.platform === "instagram")
    .map((a) => ({
      id: a.id,
      platform: a.platform as "youtube" | "instagram",
      label: a.label,
    }));

  const renderedHighlightIds = new Set(clips.map((c) => c.highlightId));

  const activePipelineJobs = jobs
    .filter((j) => j.status === "pending" || j.status === "running")
    .map((j) => {
      let clipLabel: string | undefined;
      if (j.type === "render" || j.type === "caption" || j.type === "reedit") {
        const payload = j.payloadJson as {
          clip_id?: string;
          highlight_id?: string;
        };
        let clipId = payload?.clip_id;
        if (!clipId && payload?.highlight_id) {
          clipId = clips.find((c) => c.highlightId === payload.highlight_id)?.id;
        }
        if (clipId) {
          const clip = clips.find((c) => c.id === clipId);
          const highlight = clip
            ? highlights.find((h) => h.id === clip.highlightId)
            : undefined;
          clipLabel =
            highlight?.title ||
            (highlight
              ? `Highlight @ ${formatTimecode(highlight.startSeconds)}`
              : undefined);
        }
      }
      return {
        id: j.id,
        type: j.type,
        status: j.status,
        progress: j.progress,
        progressMessage: j.progressMessage,
        errorMessage: j.errorMessage,
        clipLabel,
      };
    });

  const showPipelinePanel =
    pipelineActive ||
    activePipelineJobs.length > 0 ||
    highlights.length > 0 ||
    clipCards.length > 0;

  const twelvelabsIndexRows = video
    ? await db
        .select()
        .from(schema.externalVideoIndexes)
        .where(eq(schema.externalVideoIndexes.videoId, video.id))
        .orderBy(desc(schema.externalVideoIndexes.updatedAt))
    : [];

  const twelvelabsReadyRows = twelvelabsIndexRows.filter(
    (r) => r.status === "ready" && r.providerVideoId,
  );
  const twelvelabsIndex =
    twelvelabsReadyRows[0] ??
    twelvelabsIndexRows.find((r) => r.status === "ready") ??
    twelvelabsIndexRows[0] ??
    null;
  const twelvelabsIndexChunkCount = twelvelabsReadyRows.length;
  const twelvelabsIndexReadyCount = twelvelabsReadyRows.length;

  const visualSegmentCount = video
    ? (
        await db
          .select({ count: sql<number>`count(*)` })
          .from(schema.visualSegments)
          .where(eq(schema.visualSegments.videoId, video.id))
      )[0]?.count ?? 0
    : 0;

  const twelvelabsRunning = jobs.some(
    (j) =>
      (j.type === "twelvelabs_index" || j.type === "twelvelabs_analyze") &&
      (j.status === "pending" || j.status === "running"),
  );

  const tlConfig = getTwelveLabsConfigStatus();
  const twelvelabsEnabled = tlConfig.enabled;
  const geminiMultimodalEnabled = tlConfig.multimodalEnabled;

  const visualByType = video
    ? await db
        .select({
          segmentType: schema.visualSegments.segmentType,
          count: sql<number>`count(*)`,
        })
        .from(schema.visualSegments)
        .where(eq(schema.visualSegments.videoId, video.id))
        .groupBy(schema.visualSegments.segmentType)
        .orderBy(desc(sql`count(*)`))
        .limit(12)
    : [];

  const visualCandidateStats = video
    ? await db
        .select({
          source: schema.highlightCandidates.source,
          count: sql<number>`count(*)`,
        })
        .from(schema.highlightCandidates)
        .where(eq(schema.highlightCandidates.videoId, video.id))
        .groupBy(schema.highlightCandidates.source)
        .all()
    : [];

  const pegasusCandidateCount =
    visualCandidateStats.find((r) => r.source === "twelvelabs_pegasus")?.count ??
    0;
  const marengoCandidateCount =
    visualCandidateStats.find((r) => r.source === "twelvelabs_marengo")?.count ??
    0;
  const visualCandidateCount = visualCandidateStats
    .filter((r) => r.source.startsWith("twelvelabs"))
    .reduce((s, r) => s + Number(r.count), 0);

  const analysisJobLogs = await db
    .select({
      id: schema.jobs.id,
      type: schema.jobs.type,
      status: schema.jobs.status,
      progress: schema.jobs.progress,
      progressMessage: schema.jobs.progressMessage,
      errorMessage: schema.jobs.errorMessage,
      resultJson: schema.jobs.resultJson,
      createdAt: schema.jobs.createdAt,
      finishedAt: schema.jobs.finishedAt,
    })
    .from(schema.jobs)
    .where(
      and(
        eq(schema.jobs.projectId, id),
        inArray(schema.jobs.type, [
          "twelvelabs_index",
          "twelvelabs_analyze",
          "analyze",
        ]),
      ),
    )
    .orderBy(desc(schema.jobs.createdAt))
    .limit(8);

  const twelvelabsBadgeState = (() => {
    if (!twelvelabsEnabled) return "disabled" as const;
    if (twelvelabsRunning) return "running" as const;
    if (visualSegmentCount > 0 || twelvelabsIndex?.status === "ready")
      return "used" as const;
    if (twelvelabsIndex?.status === "failed") return "failed_open" as const;
    if (highlights.length > 0 || project.status === "ready") return "skipped" as const;
    return "skipped" as const;
  })();

  const analysisBadgeState = {
    twelvelabs: twelvelabsBadgeState,
    geminiRerank: highlights.some((h) => {
      const r = h.reasonJson as HighlightReason | null;
      return (r?.llmScore ?? 0) > 0 || r?.signals?.includes("llm_pick");
    }),
    geminiMultimodal: geminiMultimodalEnabled && highlights.length > 0,
    localSignals: highlights.length > 0 || Boolean(transcript),
  };

  const pipelineTiming =
    project.status === "ready" ? getProjectTimingBreakdown(id) : null;

  const defaultDescription = [
    project.name,
    settings.vibe ? `Vibe: ${settings.vibe}` : null,
    "Made with AiClipper.",
  ]
    .filter(Boolean)
    .join("\n\n");

  return (
    <div className="container mx-auto max-w-3xl px-4 py-10">
      <ProjectLiveProgress projectId={id} active={pipelineActive} />

      <Button variant="ghost" size="sm" className="mb-6 -ml-2" asChild>
        <Link href="/">
          <ArrowLeft className="size-4" />
          Projects
        </Link>
      </Button>

      <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{project.name}</h1>
          {project.sourceUrl && (
            <p className="mt-1 break-all text-sm text-muted-foreground">
              {project.sourceUrl}
            </p>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            Settings: {settings.topN} clips · {settings.minClipSeconds}–
            {settings.maxClipSeconds}s · {settings.aspect}
            {settings.vibe ? ` · vibe: "${settings.vibe}"` : null}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={statusVariants[project.status] ?? "secondary"}>
            {statusLabel}
          </Badge>
          <PipelineTimingDialog data={pipelineTiming} />
          <DeleteProjectDialog projectId={id} projectName={project.name} />
        </div>
      </div>

      <ProjectPipelinePanel
        projectStatus={project.status}
        hasVideo={Boolean(video)}
        hasTranscript={Boolean(transcript)}
        highlightCount={highlights.length}
        clipCount={clipCards.length}
        activeJobs={activePipelineJobs}
        show={showPipelinePanel}
        twelvelabsEnabled={twelvelabsEnabled}
        twelvelabsIndexReady={twelvelabsIndexReadyCount > 0}
        hasVisualSegments={visualSegmentCount > 0}
      />

      <ProjectAnalysisDashboard
        config={tlConfig}
        analyzeModelTier={normalizeAnalyzeModel(settings.analyzeModel)}
        indexStatus={twelvelabsIndex?.status ?? null}
        indexError={twelvelabsIndex?.errorMessage ?? null}
        providerVideoId={twelvelabsIndex?.providerVideoId ?? null}
        indexChunkCount={twelvelabsIndexChunkCount}
        indexReadyCount={twelvelabsIndexReadyCount}
        visualSegmentCount={Number(visualSegmentCount)}
        visualByType={visualByType.map((r) => ({
          segmentType: r.segmentType ?? "unknown",
          count: Number(r.count),
        }))}
        visualCandidateCount={visualCandidateCount}
        pegasusCandidateCount={Number(pegasusCandidateCount)}
        marengoCandidateCount={Number(marengoCandidateCount)}
        recentJobs={analysisJobLogs.map((j) => ({
          id: j.id,
          type: j.type,
          status: j.status,
          progress: j.progress,
          progressMessage: j.progressMessage,
          errorMessage: j.errorMessage,
          resultJson: (j.resultJson as Record<string, unknown> | null) ?? null,
          createdAt: j.createdAt,
          finishedAt: j.finishedAt,
        }))}
        projectNotes={project.notes}
        show={
          Boolean(transcript) &&
          (twelvelabsEnabled ||
            visualSegmentCount > 0 ||
            analysisJobLogs.length > 0 ||
            project.status === "analyzing")
        }
      />

      {highlights.length > 0 && (
        <Card className="mb-4 scroll-mt-28">
          <CardHeader>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <CardTitle className="flex items-center gap-2 text-base">
                  <Sparkles className="size-4" />
                  Highlight candidates
                </CardTitle>
                <CardDescription>
                  {highlights.length} clip{highlights.length === 1 ? "" : "s"} picked.
                  Click <strong>Render</strong> on any of them to cut the final
                  vertical video and (optionally) burn in captions.
                </CardDescription>
                <div className="mt-2">
                  <AnalysisBadges state={analysisBadgeState} />
                </div>
              </div>
              <ReanalyzeDialog
                projectId={id}
                savedModel={normalizeAnalyzeModel(settings.analyzeModel)}
                savedVibe={settings.vibe}
                disabled={!transcript || pipelineActive}
                disabledReason={
                  !transcript
                    ? "Wait for the transcribe step to finish (or upload your own transcript)."
                    : "Another pipeline step is already running. Try again when it finishes."
                }
              />
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {highlights.map((h, idx) => {
              const reason = h.reasonJson as HighlightReason | null;
              const duration = h.endSeconds - h.startSeconds;
              const rendered = renderedHighlightIds.has(h.id);
              return (
                <div
                  key={h.id}
                  className="rounded-md border border-border p-3"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono text-muted-foreground">
                          #{idx + 1}
                        </span>
                        <h3 className="font-medium leading-tight">
                          {h.title || `Highlight @ ${formatTimecode(h.startSeconds)}`}
                        </h3>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {formatTimecode(h.startSeconds)} →{" "}
                        {formatTimecode(h.endSeconds)} · {duration.toFixed(1)}s
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="text-lg font-semibold">
                        {(h.score * 100).toFixed(0)}
                      </div>
                      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
                        score
                      </div>
                    </div>
                  </div>
                  {h.summary && (
                    <p className="mt-2 text-sm text-foreground/90">
                      {h.summary}
                    </p>
                  )}
                  {reason && (
                    <>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {reason.signals?.map((sig, i) => (
                          <span
                            key={`${sig}-${i}`}
                            className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground"
                          >
                            {sig.replace(/_/g, " ")}
                          </span>
                        ))}
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                          audio {((reason.audioScore ?? 0) * 100).toFixed(0)}
                        </span>
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                          chat {((reason.chatScore ?? 0) * 100).toFixed(0)}
                        </span>
                        {(reason.visualScore ?? 0) > 0 && (
                          <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                            visual {((reason.visualScore ?? 0) * 100).toFixed(0)}
                          </span>
                        )}
                        {reason.llmScore > 0 && (
                          <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                            llm {((reason.llmScore ?? 0) * 100).toFixed(0)}
                          </span>
                        )}
                      </div>
                      <HighlightEvidenceDetails reason={reason} />
                    </>
                  )}
                  <div className="mt-3 flex justify-end">
                    <RenderHighlightButton
                      projectId={id}
                      highlightId={h.id}
                      alreadyRendered={rendered}
                    />
                  </div>
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {clipCards.length > 0 && (
        <Card className="mb-4 scroll-mt-28">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Film className="size-4" />
              Rendered clips ({clipCards.length})
            </CardTitle>
            <CardDescription>
              Each card is a final cut. Add captions, restyle, download, or
              schedule uploads from here.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {clipCards.map((c) => (
              <ClipCard
                key={c.id}
                projectId={id}
                clip={c}
                accounts={accountOptions}
                defaultTitle={c.title}
                defaultDescription={defaultDescription}
              />
            ))}
          </CardContent>
        </Card>
      )}

      {project.status === "ready" && highlights.length === 0 && (
        <Card className="border-dashed mb-4">
          <CardContent className="py-8 space-y-3 text-center text-sm text-muted-foreground">
            <p>
              Analysis complete but no highlights were found. The video may be
              too short for your clip-length range, or audio was uniformly
              quiet.
            </p>
            {transcript && (
              <div className="flex justify-center">
                <ReanalyzeDialog
                  projectId={id}
                  savedModel={normalizeAnalyzeModel(settings.analyzeModel)}
                  savedVibe={settings.vibe}
                />
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {video ? (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <FileVideo className="size-4" />
              Source video
            </CardTitle>
            <CardDescription>
              Stored under{" "}
              <code className="text-xs">data/videos/{video.filePath}</code>
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <span className="text-muted-foreground">Duration</span>
              <p>{formatDuration(video.durationSeconds)}</p>
            </div>
            <div>
              <span className="text-muted-foreground">Size</span>
              <p>{formatBytes(video.sizeBytes)}</p>
            </div>
            <div>
              <span className="text-muted-foreground">Resolution</span>
              <p>
                {video.width && video.height
                  ? `${video.width}×${video.height}`
                  : "—"}
              </p>
            </div>
            <div>
              <span className="text-muted-foreground">Codec / FPS</span>
              <p>
                {[video.codec, video.fps ? `${video.fps.toFixed(2)} fps` : null]
                  .filter(Boolean)
                  .join(" · ") || "—"}
              </p>
            </div>
          </CardContent>
        </Card>
      ) : (
        !pipelineActive && (
          <Card className="border-dashed mb-4">
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No video file yet.
            </CardContent>
          </Card>
        )
      )}

      {video && (
        <Card className="mb-4">
          <CardHeader>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <CardTitle className="flex items-center gap-2 text-base">
                  <FileText className="size-4" />
                  Transcript
                </CardTitle>
                <CardDescription>
                  {transcript
                    ? [
                        transcript.language?.toUpperCase(),
                        `${transcript.segmentCount} segments`,
                        transcript.model,
                      ]
                        .filter(Boolean)
                        .join(" · ")
                    : "No transcript yet — upload one to skip auto-transcription, or wait for the worker."}
                </CardDescription>
              </div>
              <TranscriptUploadDialog
                projectId={id}
                hasExistingTranscript={!!transcript}
              />
            </div>
          </CardHeader>
          {transcript && (
            <CardContent className="space-y-2">
              {transcript.fullText ? (
                <div className="max-h-48 overflow-auto rounded-md bg-muted p-3 text-sm whitespace-pre-wrap leading-relaxed">
                  {transcript.fullText.length > 1200
                    ? transcript.fullText.slice(0, 1200) + "…"
                    : transcript.fullText}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  No text extracted.
                </p>
              )}
            </CardContent>
          )}
        </Card>
      )}

      {project.notes && project.status === "failed" && (
        <Card className="mt-4 border-destructive/40">
          <CardHeader>
            <CardTitle className="text-base text-destructive">Error</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm whitespace-pre-wrap">{project.notes}</p>
          </CardContent>
        </Card>
      )}

      <p className="mt-8 text-xs text-muted-foreground">
        Created {formatDistanceToNow(project.createdAt, { addSuffix: true })}
      </p>
    </div>
  );
}

void DEFAULT_CAPTION_SETTINGS; // keep type import alive for type-only usage
