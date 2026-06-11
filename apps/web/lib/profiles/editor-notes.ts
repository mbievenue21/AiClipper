import type { TrainingEditorNotes } from "@/lib/db/schema";

export type EditorNotesInput = {
  keywords: string;
  phrases: string;
  rationale: string;
  antiKeywords: string;
  enrichWithGemini: boolean;
};

export const EMPTY_EDITOR_NOTES_INPUT: EditorNotesInput = {
  keywords: "",
  phrases: "",
  rationale: "",
  antiKeywords: "",
  enrichWithGemini: true,
};

export function parseTagList(raw: string): string[] {
  return raw
    .split(/[,;\n]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);
}

export function parsePhraseList(raw: string): string[] {
  return raw
    .split(/\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function editorNotesFromInput(input: EditorNotesInput): TrainingEditorNotes {
  return {
    userKeywords: parseTagList(input.keywords),
    userPhrases: parsePhraseList(input.phrases),
    userRationale: input.rationale.trim() || undefined,
    userAntiKeywords: parseTagList(input.antiKeywords),
    enrichWithGemini: input.enrichWithGemini,
  };
}

export function hasEditorNotesContent(notes: TrainingEditorNotes): boolean {
  return Boolean(
    notes.userKeywords?.length ||
      notes.userPhrases?.length ||
      notes.userRationale ||
      notes.userAntiKeywords?.length,
  );
}

export function mergeEditorNotesIntoFeatures(
  existing: Record<string, unknown> | null | undefined,
  notes: TrainingEditorNotes | undefined,
): Record<string, unknown> | undefined {
  if (!notes || !hasEditorNotesContent(notes)) {
    return existing ?? undefined;
  }
  return {
    ...(existing ?? {}),
    editorNotes: notes,
  };
}
