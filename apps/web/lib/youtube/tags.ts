/** YouTube Data API tag limits (videos.insert snippet.tags). */
export const YOUTUBE_MAX_TAG_LEN = 30;
export const YOUTUBE_MAX_TAGS_TOTAL_CHARS = 500;
export const YOUTUBE_MAX_TAG_COUNT = 30;

/**
 * Normalize tags for YouTube upload. Drops junk, dedupes, enforces per-tag
 * length (30) and combined length (500). Safe to call on every save/upload.
 */
export function sanitizeYouTubeTags(input: string[] | undefined): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  let totalChars = 0;

  for (const raw of input ?? []) {
    let t = String(raw).trim().toLowerCase();
    t = t.replace(/^#+/, "");
    t = t.replace(/[^a-z0-9 _-]/g, "").trim();
    t = t.replace(/\s+/g, " ");
    if (!t) continue;

    if (t.length > YOUTUBE_MAX_TAG_LEN) {
      const trimmed = t.slice(0, YOUTUBE_MAX_TAG_LEN);
      const lastSpace = trimmed.lastIndexOf(" ");
      t =
        lastSpace > 8 ? trimmed.slice(0, lastSpace).trim() : trimmed.trim();
      if (!t) continue;
    }

    if (seen.has(t)) continue;
    if (totalChars + t.length > YOUTUBE_MAX_TAGS_TOTAL_CHARS) break;

    seen.add(t);
    out.push(t);
    totalChars += t.length;
    if (out.length >= YOUTUBE_MAX_TAG_COUNT) break;
  }

  return out;
}

/** Normalize a single tag chip input; returns null if empty after cleanup. */
export function normalizeYouTubeTag(raw: string): string | null {
  return sanitizeYouTubeTags([raw])[0] ?? null;
}
