import { notFound } from "next/navigation";
import { asc, eq } from "drizzle-orm";

import { db, schema } from "@/lib/db/client";
import {
  DEFAULT_CAPTION_SETTINGS,
  type CaptionSegmentOverride,
  type ClipCaptionSettings,
} from "@/lib/db/schema";

import { ClipEditor } from "./clip-editor";

export const dynamic = "force-dynamic";

type PageProps = {
  params: Promise<{ id: string; clipId: string }>;
};

export default async function ClipEditPage({ params }: PageProps) {
  const { id: projectId, clipId } = await params;

  const [project] = await db
    .select()
    .from(schema.projects)
    .where(eq(schema.projects.id, projectId))
    .limit(1);
  if (!project) notFound();

  const [clip] = await db
    .select()
    .from(schema.clips)
    .where(eq(schema.clips.id, clipId))
    .limit(1);
  if (!clip || clip.status !== "ready") notFound();

  const [highlight] = await db
    .select()
    .from(schema.highlights)
    .where(eq(schema.highlights.id, clip.highlightId))
    .limit(1);
  if (!highlight) notFound();

  const [video] = await db
    .select()
    .from(schema.videos)
    .where(eq(schema.videos.id, highlight.videoId))
    .limit(1);
  if (!video) notFound();

  const transcript = await db
    .select()
    .from(schema.transcripts)
    .where(eq(schema.transcripts.videoId, video.id))
    .limit(1)
    .then((r) => r[0] ?? null);

  const segRows = transcript
    ? await db
        .select()
        .from(schema.transcriptSegments)
        .where(eq(schema.transcriptSegments.transcriptId, transcript.id))
        .orderBy(asc(schema.transcriptSegments.startSeconds))
    : [];

  const storedSegments = (clip.captionSegmentsJson as CaptionSegmentOverride[] | null) ?? null;

  return (
    <ClipEditor
      projectId={projectId}
      clipId={clipId}
      title={highlight.title ?? "Clip"}
      filePath={clip.filePath}
      aspect={clip.aspect}
      sourceDuration={video.durationSeconds ?? highlight.endSeconds}
      highlightStart={highlight.startSeconds}
      highlightEnd={highlight.endSeconds}
      sourceStart={clip.sourceStartSeconds}
      sourceEnd={clip.sourceEndSeconds}
      storedTrimStart={clip.trimStartSeconds ?? 0}
      storedTrimEnd={clip.trimEndSeconds ?? 0}
      storedCaptionSegments={storedSegments}
      transcriptSegments={segRows.map((s) => ({
        startSeconds: s.startSeconds,
        endSeconds: s.endSeconds,
        text: s.text,
      }))}
      captionStyle={
        (clip.captionStyleJson as ClipCaptionSettings) ?? DEFAULT_CAPTION_SETTINGS
      }
    />
  );
}
