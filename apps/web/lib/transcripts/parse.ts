/**
 * Parsers for user-uploaded transcripts.
 *
 * Accepted formats:
 *  - .srt  (SubRip)         — timestamped, one of the most common exports
 *  - .vtt  (WebVTT)         — HTML5 standard; YouTube exports use this
 *  - .json (Whisper-style)  — `[{ start, end, text, words?: [{word,start,end}] }]`
 *
 * Plain .txt is intentionally NOT supported here — without timestamps we'd
 * need forced alignment (whisperx / aeneas / wav2vec ctc) which is a much
 * bigger lift. Users uploading plain text should keep the auto-transcription
 * and just override the highlights manually.
 *
 * All times in the output are SECONDS (float) relative to the video start.
 */
import "server-only";

export type ParsedTranscriptSegment = {
  startSeconds: number;
  endSeconds: number;
  text: string;
  /** Optional per-word timing — only filled when the source had it. */
  words?: Array<{ word: string; start: number; end: number }>;
};

export type ParsedTranscript = {
  format: "srt" | "vtt" | "json";
  language: string | null;
  segments: ParsedTranscriptSegment[];
};

export class TranscriptParseError extends Error {}

/** Detect format from filename + content. */
export function detectFormat(filename: string, content: string): ParsedTranscript["format"] {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".srt")) return "srt";
  if (lower.endsWith(".vtt")) return "vtt";
  if (lower.endsWith(".json")) return "json";
  // Content sniff: WEBVTT header is unique.
  if (/^\s*WEBVTT/i.test(content)) return "vtt";
  if (/^\s*\[/.test(content) || /^\s*\{/.test(content)) return "json";
  // Default to SRT — it's the most permissive parser.
  return "srt";
}

/** "00:01:23,456" or "00:01:23.456" → seconds. */
function parseClock(s: string): number {
  const m = s.trim().match(/^(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})$/);
  if (!m) {
    // Try "MM:SS.mmm"
    const m2 = s.trim().match(/^(\d{1,2}):(\d{2})[.,](\d{1,3})$/);
    if (!m2) throw new TranscriptParseError(`Bad timestamp: ${s}`);
    return Number(m2[1]) * 60 + Number(m2[2]) + Number(m2[3]) / 1000;
  }
  const h = m[1] ? Number(m[1]) : 0;
  return h * 3600 + Number(m[2]) * 60 + Number(m[3]) + Number(m[4]) / 1000;
}

/** Strip HTML-ish tags (`<v Speaker>`, `<c.colorClass>`, `<i>`, etc.) that
 *  WebVTT permits but our renderer doesn't want. */
function stripVttTags(text: string): string {
  return text
    .replace(/<\/?[A-Za-z][^>]*>/g, "")
    // VTT styling cues like {speaker=...} aren't a thing, but be paranoid.
    .replace(/\{[^}]*\}/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function parseSrt(content: string): ParsedTranscriptSegment[] {
  const segments: ParsedTranscriptSegment[] = [];
  // Normalize line endings and split into cue blocks (blank-line separated).
  const blocks = content.replace(/\r\n?/g, "\n").trim().split(/\n{2,}/);
  for (const raw of blocks) {
    const lines = raw.split("\n").filter((l) => l.length > 0);
    if (lines.length < 2) continue;
    // Optional cue number on the first line.
    let timingLineIdx = 0;
    if (/^\d+$/.test(lines[0].trim())) timingLineIdx = 1;
    const timingLine = lines[timingLineIdx];
    const timingMatch = timingLine.match(
      /^(\S+)\s*-->\s*(\S+)/,
    );
    if (!timingMatch) continue;
    const startSeconds = parseClock(timingMatch[1]);
    const endSeconds = parseClock(timingMatch[2]);
    const text = lines
      .slice(timingLineIdx + 1)
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
    if (!text) continue;
    segments.push({ startSeconds, endSeconds, text });
  }
  return segments;
}

function parseVtt(content: string): ParsedTranscriptSegment[] {
  // VTT is SRT-like with a "WEBVTT" header, optional cue identifiers, and
  // permitted inline styling. We just strip the header and reuse SRT-ish
  // logic.
  const normalized = content.replace(/\r\n?/g, "\n");
  const withoutHeader = normalized.replace(/^WEBVTT[^\n]*\n+/i, "");
  // Drop NOTE blocks (comments).
  const withoutNotes = withoutHeader.replace(
    /(^|\n)NOTE[\s\S]*?(\n{2,}|$)/g,
    "\n",
  );
  const blocks = withoutNotes.trim().split(/\n{2,}/);
  const segments: ParsedTranscriptSegment[] = [];
  for (const raw of blocks) {
    const lines = raw.split("\n").filter((l) => l.length > 0);
    if (lines.length < 2) continue;
    // Cue id (optional) appears before the timing line and contains no `-->`.
    let timingLineIdx = lines[0].includes("-->") ? 0 : 1;
    const timingLine = lines[timingLineIdx];
    if (!timingLine || !timingLine.includes("-->")) continue;
    const [startStr, restStr] = timingLine.split("-->");
    const endStr = restStr.trim().split(/\s+/)[0]; // discard cue settings after the timing
    let startSeconds: number;
    let endSeconds: number;
    try {
      startSeconds = parseClock(startStr.trim());
      endSeconds = parseClock(endStr);
    } catch {
      continue;
    }
    const text = stripVttTags(lines.slice(timingLineIdx + 1).join(" "));
    if (!text) continue;
    segments.push({ startSeconds, endSeconds, text });
  }
  return segments;
}

function parseJsonTranscript(content: string): {
  segments: ParsedTranscriptSegment[];
  language: string | null;
} {
  let data: unknown;
  try {
    data = JSON.parse(content);
  } catch (err) {
    throw new TranscriptParseError(
      `JSON parse failed: ${(err as Error).message}`,
    );
  }
  // Two accepted shapes:
  //   1) { language?: string, segments: [...] }
  //   2) [...]   (bare array)
  let arr: unknown[];
  let language: string | null = null;
  if (Array.isArray(data)) {
    arr = data;
  } else if (data && typeof data === "object" && Array.isArray((data as { segments?: unknown[] }).segments)) {
    arr = (data as { segments: unknown[] }).segments;
    const lang = (data as { language?: unknown }).language;
    if (typeof lang === "string") language = lang;
  } else {
    throw new TranscriptParseError(
      "JSON transcript must be an array of segments or { segments: [...] }",
    );
  }
  const segments: ParsedTranscriptSegment[] = [];
  for (const item of arr) {
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    const start = Number(o.start ?? o.startSeconds ?? o.start_seconds);
    const end = Number(o.end ?? o.endSeconds ?? o.end_seconds ?? start);
    const text = String(o.text ?? "").trim();
    if (!Number.isFinite(start) || !Number.isFinite(end) || !text) continue;
    const wordsRaw = (o.words ?? o.word_timings) as unknown;
    let words: ParsedTranscriptSegment["words"];
    if (Array.isArray(wordsRaw)) {
      words = wordsRaw
        .filter((w): w is Record<string, unknown> => !!w && typeof w === "object")
        .map((w) => ({
          word: String(w.word ?? w.text ?? "").trim(),
          start: Number(w.start ?? w.startSeconds ?? w.start_seconds ?? start),
          end: Number(w.end ?? w.endSeconds ?? w.end_seconds ?? end),
        }))
        .filter((w) => w.word && Number.isFinite(w.start) && Number.isFinite(w.end));
      if (words.length === 0) words = undefined;
    }
    segments.push({
      startSeconds: start,
      endSeconds: end,
      text,
      ...(words ? { words } : {}),
    });
  }
  return { segments, language };
}

/** Top-level dispatcher. Throws TranscriptParseError on any issue. */
export function parseTranscript(
  filename: string,
  content: string,
): ParsedTranscript {
  const format = detectFormat(filename, content);
  let segments: ParsedTranscriptSegment[] = [];
  let language: string | null = null;
  if (format === "srt") {
    segments = parseSrt(content);
  } else if (format === "vtt") {
    segments = parseVtt(content);
  } else {
    const out = parseJsonTranscript(content);
    segments = out.segments;
    language = out.language;
  }
  if (segments.length === 0) {
    throw new TranscriptParseError(
      `No segments parsed from ${format.toUpperCase()} content. Is the file empty or malformed?`,
    );
  }
  // Sort + sanity-clamp: keep timestamps monotonic and non-negative.
  segments.sort((a, b) => a.startSeconds - b.startSeconds);
  for (const s of segments) {
    if (s.startSeconds < 0) s.startSeconds = 0;
    if (s.endSeconds < s.startSeconds) s.endSeconds = s.startSeconds + 0.5;
  }
  return { format, language, segments };
}
