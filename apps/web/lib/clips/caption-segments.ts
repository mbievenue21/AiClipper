import type { CaptionSegmentOverride } from "@/lib/db/schema";

type RawSegment = {
  startSeconds: number;
  endSeconds: number;
  text: string;
};

/**
 * Build clip-relative segment-level captions from transcript rows.
 * Mirrors worker chunk_segments at segment granularity (no word split).
 */
export function buildCaptionSegmentsFromTranscript(
  segments: RawSegment[],
  clipStart: number,
  clipEnd: number,
): CaptionSegmentOverride[] {
  const out: CaptionSegmentOverride[] = [];
  for (const seg of segments) {
    if (seg.endSeconds <= clipStart || seg.startSeconds >= clipEnd) continue;
    const start = Math.max(seg.startSeconds, clipStart) - clipStart;
    const end = Math.min(seg.endSeconds, clipEnd) - clipStart;
    const text = seg.text.trim();
    if (!text) continue;
    out.push({ start, end, text });
  }
  return out;
}

export function resolveEditorCaptionSegments(
  stored: CaptionSegmentOverride[] | null | undefined,
  transcriptSegments: RawSegment[],
  clipStart: number,
  clipEnd: number,
  trimStart: number,
  trimEnd: number,
  sourceStart?: number | null,
  sourceEnd?: number | null,
  /** When true (live trim drag), recompute from highlight bounds + trim. */
  preferHighlightTrim = false,
): CaptionSegmentOverride[] {
  const hasSourceWindow =
    !preferHighlightTrim && sourceStart != null && sourceEnd != null;
  const effectiveStart = hasSourceWindow ? sourceStart! : clipStart + trimStart;
  const effectiveEnd = hasSourceWindow ? sourceEnd! : clipEnd - trimEnd;
  const trimOffset = hasSourceWindow ? 0 : trimStart;
  const base =
    stored && stored.length > 0
      ? stored
      : buildCaptionSegmentsFromTranscript(
          transcriptSegments,
          effectiveStart,
          effectiveEnd,
        );

  const duration = Math.max(0.1, effectiveEnd - effectiveStart);
  return base
    .map((s) => ({
      start: Math.max(0, s.start - trimOffset),
      end: Math.min(duration, s.end - trimOffset),
      text: s.text,
    }))
    .filter((s) => s.end > s.start && s.text.trim());

}
