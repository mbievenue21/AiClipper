import Link from "next/link";
import { notFound } from "next/navigation";
import { asc, desc, eq, sql } from "drizzle-orm";
import { formatDistanceToNow } from "date-fns";
import { ArrowLeft, FileText, FileVideo, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { db, schema } from "@/lib/db/client";
import {
  DEFAULT_PROJECT_SETTINGS,
  type HighlightReason,
  type ProjectSettings,
} from "@/lib/db/schema";
import { RefreshWhenRunning } from "./refresh-when-running";

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

const JOB_LABELS: Record<string, string> = {
  ingest: "Ingest",
  transcribe: "Transcribe",
  analyze: "Analyze",
  render: "Render",
  publish: "Publish",
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
    .limit(15);

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

  const latestByType = new Map<string, (typeof jobs)[number]>();
  for (const j of jobs) if (!latestByType.has(j.type)) latestByType.set(j.type, j);

  const anyJobActive = jobs.some(
    (j) => j.status === "pending" || j.status === "running",
  );
  const pipelineActive =
    anyJobActive ||
    project.status === "ingesting" ||
    project.status === "transcribing" ||
    project.status === "analyzing";

  const statusLabel = (() => {
    if (project.status === "ready" && highlights.length > 0) return "highlights ready";
    if (project.status === "pending" && transcript) return "transcribed";
    if (project.status === "pending" && video) return "downloaded";
    if (project.status === "ready" && transcript) return "transcribed";
    return project.status;
  })();

  return (
    <div className="container mx-auto max-w-3xl px-4 py-10">
      <RefreshWhenRunning active={pipelineActive} />

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
        <Badge variant={statusVariants[project.status] ?? "secondary"}>
          {statusLabel}
        </Badge>
      </div>

      {Array.from(latestByType.values()).map((j) => (
        <Card key={j.id} className="mb-4">
          <CardHeader className="pb-2">
            <CardTitle className="text-base">
              {JOB_LABELS[j.type] ?? j.type} job
            </CardTitle>
            <CardDescription>
              {j.status}
              {j.progressMessage ? ` — ${j.progressMessage}` : null}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Progress value={Math.round(j.progress * 100)} />
            <p className="text-xs text-muted-foreground">
              {Math.round(j.progress * 100)}% · job {j.id}
            </p>
            {j.errorMessage && (
              <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 text-xs whitespace-pre-wrap">
                {j.errorMessage}
              </pre>
            )}
          </CardContent>
        </Card>
      ))}

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
            {video.audioPath && (
              <div className="sm:col-span-2">
                <span className="text-muted-foreground">Audio track</span>
                <p>
                  <code className="text-xs">{video.audioPath}</code>
                </p>
              </div>
            )}
            {video.chatJsonPath && (
              <div className="sm:col-span-2">
                <span className="text-muted-foreground">Chat replay</span>
                <p>
                  <code className="text-xs">{video.chatJsonPath}</code>
                </p>
              </div>
            )}
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

      {transcript && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <FileText className="size-4" />
              Transcript
            </CardTitle>
            <CardDescription>
              {[
                transcript.language?.toUpperCase(),
                `${transcript.segmentCount} segments`,
                transcript.model,
              ]
                .filter(Boolean)
                .join(" · ")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {transcript.fullText ? (
              <div className="max-h-48 overflow-auto rounded-md bg-muted p-3 text-sm whitespace-pre-wrap leading-relaxed">
                {transcript.fullText.length > 1200
                  ? transcript.fullText.slice(0, 1200) + "…"
                  : transcript.fullText}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No text extracted.</p>
            )}
          </CardContent>
        </Card>
      )}

      {highlights.length > 0 && (
        <Card className="mb-4">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Sparkles className="size-4" />
              Highlight candidates
            </CardTitle>
            <CardDescription>
              Top {highlights.length} clip{highlights.length === 1 ? "" : "s"}{" "}
              picked from the transcript, audio, and chat signals. Step 8 will
              render the ones you approve.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {highlights.map((h, idx) => {
              const reason = h.reasonJson as HighlightReason | null;
              const duration = h.endSeconds - h.startSeconds;
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
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {reason.signals?.map((sig) => (
                        <span
                          key={sig}
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
                      {reason.llmScore > 0 && (
                        <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                          llm {((reason.llmScore ?? 0) * 100).toFixed(0)}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            <p className="text-xs text-muted-foreground">
              Step 8 (clip render) will turn the approved ones into actual{" "}
              {settings.aspect} video files with captions.
            </p>
          </CardContent>
        </Card>
      )}

      {project.status === "ready" && highlights.length === 0 && (
        <Card className="border-dashed mb-4">
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            Analysis complete but no highlights were found. The video may be too
            short for your clip-length range, or audio was uniformly quiet.
          </CardContent>
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
