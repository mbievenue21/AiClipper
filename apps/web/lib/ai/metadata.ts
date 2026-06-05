/**
 * AI-generated YouTube/short-form upload metadata.
 *
 * Talks to Gemini 2.5 Flash via its REST endpoint (no SDK dependency)
 * with structured-JSON output so we don't have to parse markdown noise.
 *
 * Why Gemini Flash vs another model
 * ---------------------------------
 * - Already in this project for highlight analysis — one API key, one
 *   billing line.
 * - Free tier is generous; latency ~1–3s for short prompts.
 * - Structured-output (`responseMimeType: application/json` +
 *   `responseSchema`) is rock-solid as of 2.5 — we never have to
 *   regex-parse code fences.
 *
 * Style direction
 * ---------------
 * Prompts target YouTube Shorts conventions because that's the dominant
 * upload destination right now: <60 char title, hook in first 2 lines of
 * description, mix of broad + niche tags. Same metadata serves IG Reels
 * fine (the description doubles as the caption with hashtags appended).
 */
import "server-only";

import { db, schema } from "@/lib/db/client";
import {
  DEFAULT_PROJECT_SETTINGS,
  type GeneratedUploadMetadata,
  type ProjectSettings,
} from "@/lib/db/schema";
import { asc, eq } from "drizzle-orm";

// Newest stable Flash. Override with GEMINI_FLASH_MODEL if you want.
// https://ai.google.dev/gemini-api/docs/models
const MODEL = process.env.GEMINI_FLASH_MODEL || "gemini-3.5-flash";
const ENDPOINT = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`;
const IS_GEMINI_3 = MODEL.toLowerCase().startsWith("gemini-3");

/** JSON schema we ask Gemini to fill. Loose enough to allow creativity, strict enough to typecheck. */
const RESPONSE_SCHEMA = {
  type: "object",
  properties: {
    title: {
      type: "string",
      description:
        "Eye-catching title under 70 chars. Curiosity gap, emotion, specifics, or numbers. No clickbait that misrepresents.",
    },
    hook: {
      type: "string",
      description:
        "One short sentence (under 90 chars) for the opening line of the description. Should make a scroller pause.",
    },
    description: {
      type: "string",
      description:
        "Full description (300–600 chars). Line 1 = hook. Line 2 = tease/context. Line 3 = CTA (like/follow). Plain text — no markdown.",
    },
    tags: {
      type: "array",
      items: { type: "string" },
      description:
        "10–15 YouTube tags. Mix broad (1–2 word, e.g. 'comedy') and specific (3+ word, e.g. 'speedrun world record'). No leading #.",
    },
    hashtags: {
      type: "array",
      items: { type: "string" },
      description:
        "3–6 hashtags, no leading #. Surfaced separately so IG/Shorts captions can pin them at the end.",
    },
  },
  required: ["title", "description", "tags"],
};

export class MetadataGenerationError extends Error {}

export type GenerateMetadataInput = {
  highlightId: string;
};

export type MetadataPayload = Omit<GeneratedUploadMetadata, "model" | "generatedAt" | "version">;

/** Pull the highlight + its transcript window + the project's vibe setting. */
function loadContext(highlightId: string) {
  const h = db
    .select({
      id: schema.highlights.id,
      title: schema.highlights.title,
      summary: schema.highlights.summary,
      startSeconds: schema.highlights.startSeconds,
      endSeconds: schema.highlights.endSeconds,
      reasonJson: schema.highlights.reasonJson,
      videoId: schema.highlights.videoId,
    })
    .from(schema.highlights)
    .where(eq(schema.highlights.id, highlightId))
    .limit(1)
    .all()[0];

  if (!h) {
    throw new MetadataGenerationError(`Highlight ${highlightId} not found.`);
  }

  const video = db
    .select({
      projectId: schema.videos.projectId,
    })
    .from(schema.videos)
    .where(eq(schema.videos.id, h.videoId))
    .limit(1)
    .all()[0];

  let project: { id: string; name: string; settings: ProjectSettings } | null = null;
  if (video?.projectId) {
    const p = db
      .select({
        id: schema.projects.id,
        name: schema.projects.name,
        settingsJson: schema.projects.settingsJson,
      })
      .from(schema.projects)
      .where(eq(schema.projects.id, video.projectId))
      .limit(1)
      .all()[0];
    if (p) {
      const parsed =
        p.settingsJson && typeof p.settingsJson === "object"
          ? (p.settingsJson as Partial<ProjectSettings>)
          : {};
      project = {
        id: p.id,
        name: p.name,
        settings: { ...DEFAULT_PROJECT_SETTINGS, ...parsed },
      };
    }
  }

  // Transcript segments that overlap the highlight time range.
  const transcript = db
    .select({
      id: schema.transcripts.id,
    })
    .from(schema.transcripts)
    .where(eq(schema.transcripts.videoId, h.videoId))
    .limit(1)
    .all()[0];

  let transcriptText = "";
  if (transcript) {
    const segs = db
      .select({
        startSeconds: schema.transcriptSegments.startSeconds,
        endSeconds: schema.transcriptSegments.endSeconds,
        text: schema.transcriptSegments.text,
      })
      .from(schema.transcriptSegments)
      .where(eq(schema.transcriptSegments.transcriptId, transcript.id))
      .orderBy(asc(schema.transcriptSegments.startSeconds))
      .all();
    const lines = segs
      .filter(
        (s) =>
          s.endSeconds >= h.startSeconds - 2 &&
          s.startSeconds <= h.endSeconds + 2,
      )
      .map((s) => s.text.trim())
      .filter(Boolean);
    transcriptText = lines.join(" ");
  }

  return { highlight: h, project, transcriptText };
}

function buildPrompt(input: ReturnType<typeof loadContext>): string {
  const { highlight, project, transcriptText } = input;
  const durationSec = Math.max(0, highlight.endSeconds - highlight.startSeconds);
  const vibe = project?.settings.vibe?.trim();
  const aspect = project?.settings.aspect ?? "9:16";

  // Cap transcript at ~2000 chars to stay well inside Gemini's free-tier
  // token budget. For typical 20–60s clips this is more than enough.
  const transcriptExcerpt =
    transcriptText.length > 2000
      ? transcriptText.slice(0, 2000) + "…"
      : transcriptText;

  // Why we hint about platform: 9:16 → Shorts/Reels (mobile-first, snappy).
  // 16:9 → desktop YouTube (slightly different conventions).
  const platformHint =
    aspect === "9:16"
      ? "vertical short-form (YouTube Shorts / Instagram Reels)"
      : aspect === "1:1"
        ? "square format (Instagram / mixed)"
        : "horizontal YouTube long-form";

  return [
    "You are an expert short-form video editor who writes high-CTR upload metadata.",
    "Generate eye-catching title + description + tags for this clip.",
    "",
    "STYLE",
    "- Title under 70 chars. Specific, emotional, curiosity-driven. Numbers when natural. NO clickbait that misrepresents the clip. NO emojis. NO leading 'POV:' unless transcript supports it.",
    "- Description 300–600 chars, plain text only, three short paragraphs:",
    "    line 1 = the hook (matches `hook` field — a 1-sentence pattern-interrupt)",
    "    line 2 = a sentence of context/setup that teases without spoiling",
    "    line 3 = a CTA (subscribe / follow / drop a comment)",
    "- 10–15 tags. Mix of broad (1–2 words) and long-tail (3+ words). Pull actual nouns/proper nouns from the transcript. No hashtags here, no leading #.",
    "- 3–6 hashtags pulled from the most-searched short-form topics the clip fits. Plain words, no leading #. Avoid generic spam like #fyp #viral unless the transcript truly fits.",
    "",
    "AVOID",
    "- generic phrases like 'you won't believe', 'must watch', 'this happened', 'wait for it'",
    "- ALL CAPS title",
    "- repeating the exact same words across title/description/tags more than needed",
    "- spoilers in the title (keep curiosity)",
    "",
    "CONTEXT",
    `Format: ${platformHint}`,
    `Clip duration: ${durationSec.toFixed(1)}s`,
    project?.name ? `Source project: ${project.name}` : "",
    vibe ? `User's vibe hint for this project: "${vibe}"` : "",
    highlight.title ? `Working highlight title (replace this with something better): ${highlight.title}` : "",
    highlight.summary ? `AI summary of the moment: ${highlight.summary}` : "",
    "",
    "TRANSCRIPT (use as primary truth — only claim what's here)",
    transcriptExcerpt
      ? `"""\n${transcriptExcerpt}\n"""`
      : "(no transcript available — generate something generic based on summary above)",
  ]
    .filter(Boolean)
    .join("\n");
}

/**
 * Try to parse `raw` as JSON. If it fails because the string was cut off mid-
 * value (the common Gemini truncation failure mode), walk the string and
 * close any open string/array/object so we still get partial-but-usable
 * metadata back instead of throwing. The user can still regenerate.
 */
function tryParseJsonWithSalvage(raw: string): MetadataPayload {
  // First attempt: strict.
  try {
    return JSON.parse(raw) as MetadataPayload;
  } catch {
    // fall through to salvage
  }

  // Walk the text and track open structures + whether we're inside a string.
  // We only care about non-escaped quotes/brackets at the top level of
  // string state, mirroring the JSON spec just deeply enough to close
  // whatever's still open at EOF.
  let inString = false;
  let escape = false;
  const stack: ("}" | "]")[] = [];
  let lastCommaIdx = -1;

  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (inString) {
      if (escape) {
        escape = false;
      } else if (ch === "\\") {
        escape = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
    } else if (ch === "{") {
      stack.push("}");
    } else if (ch === "[") {
      stack.push("]");
    } else if (ch === "}" || ch === "]") {
      stack.pop();
    } else if (ch === ",") {
      lastCommaIdx = i;
    }
  }

  // Reconstruct: close the open string, drop a trailing comma if any,
  // then append every needed close-bracket.
  let salvaged = raw;
  if (inString) salvaged += '"';
  // If a value got cut off mid-write, the last char before EOF is often a
  // dangling comma → drop everything after it so JSON.parse doesn't choke.
  // Only do this if there's nothing closing-ish between the comma and EOF.
  if (!inString && lastCommaIdx >= 0) {
    const tail = salvaged.slice(lastCommaIdx + 1).trim();
    if (tail === "" || /^[^}\]]+$/.test(tail)) {
      salvaged = salvaged.slice(0, lastCommaIdx);
    }
  }
  for (const c of stack.reverse()) salvaged += c;

  try {
    return JSON.parse(salvaged) as MetadataPayload;
  } catch (err) {
    throw new Error(
      `JSON.parse failed after salvage (${(err as Error).message}). ` +
        `First 200 chars: ${salvaged.slice(0, 200)}`,
    );
  }
}

/** Trim, dedupe, lowercase tags; strip leading # and stray punctuation. */
function sanitizeTags(input: string[] | undefined): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of input ?? []) {
    let t = String(raw).trim().toLowerCase();
    t = t.replace(/^#+/, "");
    t = t.replace(/[^a-z0-9\s_-]/g, "").trim();
    t = t.replace(/\s+/g, " ");
    if (!t) continue;
    const key = t;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(t);
    if (out.length >= 20) break;
  }
  return out;
}

function sanitize(payload: MetadataPayload): MetadataPayload {
  return {
    title: payload.title.trim().slice(0, 100),
    hook: payload.hook?.trim().slice(0, 120),
    description: payload.description.trim().slice(0, 5000),
    tags: sanitizeTags(payload.tags),
    hashtags: sanitizeTags(payload.hashtags),
  };
}

/**
 * Call Gemini Flash with a JSON-mode schema constraint. Throws
 * MetadataGenerationError on any non-200 or malformed-JSON response so
 * the server action can render the error to the user.
 */
export async function generateMetadata(
  input: GenerateMetadataInput,
): Promise<GeneratedUploadMetadata> {
  const apiKey = process.env.GEMINI_API_KEY?.trim();
  if (!apiKey) {
    throw new MetadataGenerationError(
      "GEMINI_API_KEY is not set. Add it to .env and restart the dev server.",
    );
  }

  const ctx = loadContext(input.highlightId);
  const prompt = buildPrompt(ctx);

  // Why these settings:
  // - `thinkingConfig.thinkingBudget = 0` disables Gemini 2.5 Flash's hidden
  //   reasoning pass. By default thinking tokens are deducted from
  //   `maxOutputTokens` BEFORE the visible JSON is emitted, which is what
  //   was causing truncated strings (the description was being chopped mid-
  //   quote). We don't need chain-of-thought for structured formatting.
  // - `maxOutputTokens: 2048` is well above the typical payload (~600 tokens
  //   for a full title+description+tags+hashtags blob) — leaves runway for
  //   chatty models without enabling thinking.
  // - `responseSchema` is enforced by Gemini's server-side JSON-mode, so the
  //   only failure path is truncation (handled below) or a refusal.
  // Gemini 3.x: use thinkingLevel (drop temperature/topP — optimized for
  // defaults). Gemini 2.5: temperature + thinkingBudget=0 to skip the hidden
  // reasoning pass that was truncating the JSON payload.
  const generationConfig: Record<string, unknown> = {
    responseMimeType: "application/json",
    responseSchema: RESPONSE_SCHEMA,
    maxOutputTokens: 2048,
  };
  if (IS_GEMINI_3) {
    generationConfig.thinkingConfig = { thinkingLevel: "low" };
  } else {
    generationConfig.temperature = 0.85;
    generationConfig.topP = 0.95;
    generationConfig.thinkingConfig = { thinkingBudget: 0 };
  }
  const body = {
    contents: [{ role: "user", parts: [{ text: prompt }] }],
    generationConfig,
  };

  const url = `${ENDPOINT}?key=${encodeURIComponent(apiKey)}`;
  let resp: Response;
  try {
    resp = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      // 25s — Gemini Flash p99 is ~5s but cold-start tail can be longer.
      signal: AbortSignal.timeout(25_000),
    });
  } catch (err) {
    throw new MetadataGenerationError(
      "Gemini request failed: " +
        (err instanceof Error ? err.message : String(err)),
    );
  }

  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new MetadataGenerationError(
      `Gemini API ${resp.status}: ${text.slice(0, 300)}`,
    );
  }

  let parsed: MetadataPayload;
  let finishReason: string | undefined;
  let rawText = "";
  try {
    const json = (await resp.json()) as {
      candidates?: {
        content?: { parts?: { text?: string }[] };
        finishReason?: string;
      }[];
      promptFeedback?: { blockReason?: string };
    };
    if (json.promptFeedback?.blockReason) {
      throw new MetadataGenerationError(
        `Gemini blocked the prompt: ${json.promptFeedback.blockReason}`,
      );
    }
    finishReason = json.candidates?.[0]?.finishReason;
    rawText = json.candidates?.[0]?.content?.parts?.[0]?.text ?? "";
    if (!rawText) {
      throw new MetadataGenerationError(
        `Gemini returned no content (finishReason=${finishReason ?? "unknown"}). ` +
          "Often means MAX_TOKENS was hit; try a shorter transcript.",
      );
    }
    parsed = tryParseJsonWithSalvage(rawText);
  } catch (err) {
    if (err instanceof MetadataGenerationError) throw err;
    const hint =
      finishReason && finishReason !== "STOP"
        ? ` (finishReason=${finishReason})`
        : "";
    throw new MetadataGenerationError(
      `Could not parse Gemini response as JSON${hint}: ` +
        (err instanceof Error ? err.message : String(err)),
    );
  }

  const clean = sanitize(parsed);
  if (!clean.title || !clean.description || clean.tags.length === 0) {
    throw new MetadataGenerationError(
      "Gemini returned incomplete metadata (missing title, description, or tags).",
    );
  }

  const meta: GeneratedUploadMetadata = {
    ...clean,
    model: MODEL,
    generatedAt: Date.now(),
    version: 1,
  };

  db.update(schema.highlights)
    .set({ generatedMetadataJson: meta })
    .where(eq(schema.highlights.id, input.highlightId))
    .run();

  return meta;
}
