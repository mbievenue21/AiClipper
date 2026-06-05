/**
 * Trending-tag fetcher backed by YouTube Data API v3.
 *
 * Strategy
 * --------
 * 1. Call `videos.list?chart=mostPopular&part=snippet&maxResults=50` per
 *    region. Each call costs 1 quota unit; default daily quota is 10,000
 *    units so we have orders of magnitude of headroom.
 * 2. Flatten every video's `snippet.tags` into a frequency map. Score is
 *    just count-of-occurrences (top videos and bottom videos contribute
 *    equally — simple, predictable, sortable).
 * 3. Filter junk (too short, all-numeric, contains punctuation).
 * 4. Cache in a module-level Map keyed by region for 1 hour. Trending lists
 *    don't shift faster than that, and Next.js process restarts will refresh
 *    everything anyway.
 *
 * The result is purely metadata for the UI's "Suggested tags" chips — never
 * persisted to the user's clip metadata until they click a chip.
 */
import "server-only";

import { youtubeFetch, YouTubeClientError } from "./client";
import { YOUTUBE_MAX_TAG_LEN } from "./tags";

const CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

type Cached = { tags: TrendingTag[]; fetchedAt: number };
const cache = new Map<string, Cached>();

export type TrendingTag = {
  tag: string;
  /** How many of the top 50 trending videos mention it. */
  count: number;
};

type VideoListResponse = {
  items?: Array<{
    snippet?: {
      tags?: string[];
      categoryId?: string;
      defaultAudioLanguage?: string;
    };
  }>;
};

const JUNK_TAG_PATTERN = /^[\d\s]+$|^[^a-z0-9]+$/i;

/** Normalize + reject obvious junk. Tags are lowercased to match our schema. */
function normalizeTag(raw: string): string | null {
  const t = raw.trim().toLowerCase();
  if (!t) return null;
  if (t.length < 2 || t.length > YOUTUBE_MAX_TAG_LEN) return null;
  if (JUNK_TAG_PATTERN.test(t)) return null;
  // Drop tags that are purely punctuation or single characters once stripped.
  const stripped = t.replace(/[^a-z0-9 _-]/g, "").trim();
  if (stripped.length < 2) return null;
  return stripped;
}

/** Fetch raw trending video tags from YouTube Data API. */
async function fetchTrendingFromApi(regionCode: string): Promise<TrendingTag[]> {
  const url = new URL("https://www.googleapis.com/youtube/v3/videos");
  url.searchParams.set("part", "snippet");
  url.searchParams.set("chart", "mostPopular");
  url.searchParams.set("regionCode", regionCode);
  url.searchParams.set("maxResults", "50");

  const resp = await youtubeFetch(url.toString());
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new YouTubeClientError(
      `YouTube trending API ${resp.status}: ${text.slice(0, 200)}`,
    );
  }
  const data = (await resp.json()) as VideoListResponse;

  // Count tag frequencies across all returned videos.
  const counts = new Map<string, number>();
  for (const item of data.items ?? []) {
    for (const raw of item.snippet?.tags ?? []) {
      const norm = normalizeTag(raw);
      if (!norm) continue;
      counts.set(norm, (counts.get(norm) ?? 0) + 1);
    }
  }

  // Drop singletons (tags that appear in only ONE trending video — too noisy
  // and rarely useful). Then sort by count desc.
  return [...counts.entries()]
    .filter(([, n]) => n >= 2)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 60)
    .map(([tag, count]) => ({ tag, count }));
}

/**
 * Public entrypoint. Returns trending tags for the region, using the cache
 * if fresh. `force` bypasses the cache (debug + manual refresh).
 */
export async function getTrendingTags(
  regionCode: string = "US",
  { force = false }: { force?: boolean } = {},
): Promise<TrendingTag[]> {
  const region = (regionCode || "US").toUpperCase();
  const cached = cache.get(region);
  if (!force && cached && Date.now() - cached.fetchedAt < CACHE_TTL_MS) {
    return cached.tags;
  }
  const tags = await fetchTrendingFromApi(region);
  cache.set(region, { tags, fetchedAt: Date.now() });
  return tags;
}

/**
 * Filter trending tags down to ones that are likely RELEVANT to the clip.
 * Cheap heuristic: anything whose tokens overlap any token in the clip's
 * title/description/tags/transcript-derived seed words gets boosted; the
 * rest is shown in a "general" bucket. Caller decides how many to show.
 */
export function rankTrendingByRelevance(
  trending: TrendingTag[],
  seedWords: string[],
): { relevant: TrendingTag[]; general: TrendingTag[] } {
  const seedSet = new Set<string>();
  for (const word of seedWords) {
    for (const tok of word.toLowerCase().split(/[\s_-]+/)) {
      if (tok.length >= 3) seedSet.add(tok);
    }
  }
  const relevant: TrendingTag[] = [];
  const general: TrendingTag[] = [];
  for (const t of trending) {
    const tokens = t.tag.split(/[\s_-]+/);
    if (tokens.some((tok) => seedSet.has(tok))) {
      relevant.push(t);
    } else {
      general.push(t);
    }
  }
  return { relevant, general };
}
