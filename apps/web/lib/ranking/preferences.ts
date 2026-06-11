import { eq } from "drizzle-orm";

import { db, schema } from "@/lib/db/client";
import {
  DEFAULT_RANKING_WEIGHTS,
  type ClipSignalVotes,
  type HighlightReason,
  type RankingWeights,
} from "@/lib/db/schema";
import { nanoid } from "nanoid";

const PREFS_ID = "default";
const LEARNING_RATE = 0.12;
const BOUNDARY_LEARNING_RATE = 0.2;

export type RankingPreferencesSnapshot = {
  weights: RankingWeights;
  learnedPreRollSeconds: number;
  learnedTailPaddingSeconds: number;
  editorPadBeforeSeconds: number;
  editorPadAfterSeconds: number;
  feedbackCount: number;
};

export function mergeWeights(
  base: RankingWeights,
  patch: Partial<RankingWeights>,
): RankingWeights {
  return { ...base, ...patch };
}

function clamp01(n: number) {
  return Math.max(0.01, Math.min(0.99, n));
}

function normalizeFusionWeights(w: RankingWeights): RankingWeights {
  const keys = [
    "fusionVisual",
    "fusionChat",
    "fusionAudio",
    "fusionTranscript",
    "fusionAlignment",
    "fusionScene",
    "fusionAgreement",
  ] as const;
  const sum = keys.reduce((s, k) => s + w[k], 0);
  if (sum <= 0) return { ...w, ...DEFAULT_RANKING_WEIGHTS };
  const scale = 1.0 / sum;
  const out = { ...w };
  for (const k of keys) {
    out[k] = Math.round(out[k] * scale * 1000) / 1000;
  }
  return out;
}

function normalizeCandidateWeights(w: RankingWeights): RankingWeights {
  const out = { ...w };
  const chatSum = out.candidateChatAudio + out.candidateChat + out.candidateKeyword;
  if (chatSum > 0) {
    const scale = 1.0 / chatSum;
    out.candidateChatAudio = out.candidateChatAudio * scale;
    out.candidateChat = out.candidateChat * scale;
    out.candidateKeyword = out.candidateKeyword * scale;
  }
  const noChatSum = out.candidateAudio + out.candidateKeyword;
  if (noChatSum > 0) {
    const scale = 1.0 / noChatSum;
    out.candidateAudio = out.candidateAudio * scale;
    out.candidateKeyword = out.candidateKeyword * scale;
  }
  const blend = out.geminiBlendLlm + out.geminiBlendLocal;
  if (blend > 0) {
    out.geminiBlendLlm = out.geminiBlendLlm / blend;
    out.geminiBlendLocal = out.geminiBlendLocal / blend;
  }
  return out;
}

export async function getRankingPreferences(): Promise<RankingPreferencesSnapshot> {
  const row = await db
    .select()
    .from(schema.rankingPreferences)
    .where(eq(schema.rankingPreferences.id, PREFS_ID))
    .limit(1)
    .then((r) => r[0] ?? null);

  if (!row) {
    return {
      weights: { ...DEFAULT_RANKING_WEIGHTS },
      learnedPreRollSeconds: 8,
      learnedTailPaddingSeconds: 2,
      editorPadBeforeSeconds: 10,
      editorPadAfterSeconds: 10,
      feedbackCount: 0,
    };
  }

  return {
    weights: mergeWeights(DEFAULT_RANKING_WEIGHTS, row.weightsJson ?? {}),
    learnedPreRollSeconds: row.learnedPreRollSeconds ?? 8,
    learnedTailPaddingSeconds: row.learnedTailPaddingSeconds ?? 2,
    editorPadBeforeSeconds: row.editorPadBeforeSeconds ?? 10,
    editorPadAfterSeconds: row.editorPadAfterSeconds ?? 10,
    feedbackCount: row.feedbackCount ?? 0,
  };
}

function signalScores(reason: HighlightReason | null | undefined) {
  const s = reason?.scores;
  return {
    visual: s?.visual ?? reason?.visualScore ?? 0,
    audio: s?.audio ?? reason?.audioScore ?? 0,
    chat: s?.chat ?? reason?.chatScore ?? 0,
    transcript: s?.transcript ?? 0,
    fusion: s?.fusion ?? reason?.fusionScore ?? 0,
    gemini: reason?.llmScore ?? 0,
  };
}

function nudgeWeight(current: number, delta: number) {
  return clamp01(current + delta);
}

export function applyFeedbackToWeights(
  weights: RankingWeights,
  reason: HighlightReason | null | undefined,
  signalVotes: ClipSignalVotes,
  overallVote?: "up" | "down" | null,
): RankingWeights {
  let w = { ...weights };
  const scores = signalScores(reason);

  const fusionMap: Record<string, keyof RankingWeights> = {
    visual: "fusionVisual",
    audio: "fusionAudio",
    chat: "fusionChat",
    transcript: "fusionTranscript",
    fusion: "fusionVisual",
  };

  for (const [signal, vote] of Object.entries(signalVotes)) {
    if (!vote || vote === "skip") continue;
    const score = scores[signal as keyof typeof scores] ?? 0.5;
    const key = fusionMap[signal];
    if (!key) continue;
    const dir = vote === "up" ? 1 : -1;
    w[key] = nudgeWeight(w[key], dir * LEARNING_RATE * score);
    if (signal === "audio") {
      w.candidateAudio = nudgeWeight(
        w.candidateAudio,
        dir * LEARNING_RATE * score,
      );
      w.candidateChatAudio = nudgeWeight(
        w.candidateChatAudio,
        dir * LEARNING_RATE * score * 0.5,
      );
    }
    if (signal === "chat") {
      w.candidateChat = nudgeWeight(w.candidateChat, dir * LEARNING_RATE * score);
    }
    if (signal === "transcript") {
      w.candidateKeyword = nudgeWeight(
        w.candidateKeyword,
        dir * LEARNING_RATE * score,
      );
      w.fusionTranscript = nudgeWeight(
        w.fusionTranscript,
        dir * LEARNING_RATE * score,
      );
    }
    if (signal === "gemini") {
      w.geminiBlendLlm = nudgeWeight(
        w.geminiBlendLlm,
        dir * LEARNING_RATE * score,
      );
      w.geminiBlendLocal = 1 - w.geminiBlendLlm;
    }
  }

  if (overallVote === "up") {
    const ranked = Object.entries(scores).sort((a, b) => b[1] - a[1]);
    for (const [signal] of ranked.slice(0, 2)) {
      const key = fusionMap[signal];
      if (key) w[key] = nudgeWeight(w[key], LEARNING_RATE * 0.5);
    }
  } else if (overallVote === "down") {
    const top = Object.entries(scores).sort((a, b) => b[1] - a[1])[0];
    if (top) {
      const key = fusionMap[top[0]];
      if (key) w[key] = nudgeWeight(w[key], -LEARNING_RATE * 0.5);
    }
  }

  w = normalizeFusionWeights(w);
  w = normalizeCandidateWeights(w);
  return w;
}

export function applyBoundaryLearning(
  prefs: RankingPreferencesSnapshot,
  input: {
    highlightStart: number;
    highlightEnd: number;
    cutStart: number;
    cutEnd: number;
  },
): Pick<
  RankingPreferencesSnapshot,
  "learnedPreRollSeconds" | "learnedTailPaddingSeconds"
> {
  const leadIn = Math.max(0, input.highlightStart - input.cutStart);
  const tailOut = Math.max(0, input.cutEnd - input.highlightEnd);

  return {
    learnedPreRollSeconds: Math.round(
      Math.max(
        0,
        Math.min(
          20,
          prefs.learnedPreRollSeconds * (1 - BOUNDARY_LEARNING_RATE) +
            leadIn * BOUNDARY_LEARNING_RATE,
        ),
      ) * 10,
    ) / 10,
    learnedTailPaddingSeconds: Math.round(
      Math.max(
        0,
        Math.min(
          10,
          prefs.learnedTailPaddingSeconds * (1 - BOUNDARY_LEARNING_RATE) +
            tailOut * BOUNDARY_LEARNING_RATE,
        ),
      ) * 10,
    ) / 10,
  };
}

export async function persistRankingPreferences(
  prefs: RankingPreferencesSnapshot,
): Promise<void> {
  const now = new Date();
  const existing = await db
    .select({ id: schema.rankingPreferences.id })
    .from(schema.rankingPreferences)
    .where(eq(schema.rankingPreferences.id, PREFS_ID))
    .limit(1)
    .then((r) => r[0] ?? null);

  if (existing) {
    await db
      .update(schema.rankingPreferences)
      .set({
        weightsJson: prefs.weights,
        learnedPreRollSeconds: prefs.learnedPreRollSeconds,
        learnedTailPaddingSeconds: prefs.learnedTailPaddingSeconds,
        editorPadBeforeSeconds: prefs.editorPadBeforeSeconds,
        editorPadAfterSeconds: prefs.editorPadAfterSeconds,
        feedbackCount: prefs.feedbackCount,
        updatedAt: now,
      })
      .where(eq(schema.rankingPreferences.id, PREFS_ID));
  } else {
    await db.insert(schema.rankingPreferences).values({
      id: PREFS_ID,
      weightsJson: prefs.weights,
      learnedPreRollSeconds: prefs.learnedPreRollSeconds,
      learnedTailPaddingSeconds: prefs.learnedTailPaddingSeconds,
      editorPadBeforeSeconds: prefs.editorPadBeforeSeconds,
      editorPadAfterSeconds: prefs.editorPadAfterSeconds,
      feedbackCount: prefs.feedbackCount,
      updatedAt: now,
    });
  }
}

export async function recordClipFeedback(input: {
  clipId: string;
  highlightId: string;
  projectId: string;
  overallVote?: "up" | "down";
  signalVotes?: ClipSignalVotes;
  highlightStart: number;
  highlightEnd: number;
  sourceStart?: number | null;
  sourceEnd?: number | null;
  reason?: HighlightReason | null;
  notes?: string;
  applyLearning?: boolean;
}) {
  const prefs = await getRankingPreferences();
  const cutStart = input.sourceStart ?? input.highlightStart;
  const cutEnd = input.sourceEnd ?? input.highlightEnd;
  const boundaries = applyBoundaryLearning(prefs, {
    highlightStart: input.highlightStart,
    highlightEnd: input.highlightEnd,
    cutStart,
    cutEnd,
  });

  let nextWeights = prefs.weights;
  if (input.applyLearning !== false) {
    nextWeights = applyFeedbackToWeights(
      prefs.weights,
      input.reason,
      input.signalVotes ?? {},
      input.overallVote,
    );
  }

  const effectivePreRoll = Math.max(0, input.highlightStart - cutStart);
  const effectiveTail = Math.max(0, cutEnd - input.highlightEnd);

  await db.insert(schema.clipFeedback).values({
    id: nanoid(12),
    clipId: input.clipId,
    highlightId: input.highlightId,
    projectId: input.projectId,
    overallVote: input.overallVote ?? null,
    signalVotesJson: input.signalVotes ?? {},
    effectivePreRollSeconds: effectivePreRoll,
    effectiveTailSeconds: effectiveTail,
    highlightStartSeconds: input.highlightStart,
    highlightEndSeconds: input.highlightEnd,
    sourceStartSeconds: cutStart,
    sourceEndSeconds: cutEnd,
    reasonSnapshotJson: input.reason ?? null,
    notes: input.notes ?? null,
  });

  if (input.applyLearning !== false) {
    await persistRankingPreferences({
      ...prefs,
      weights: nextWeights,
      ...boundaries,
      feedbackCount: prefs.feedbackCount + 1,
    });
  }

  return {
    effectivePreRoll,
    effectiveTail,
    learnedPreRollSeconds: boundaries.learnedPreRollSeconds,
    learnedTailPaddingSeconds: boundaries.learnedTailPaddingSeconds,
  };
}
