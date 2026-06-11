"use client";

import type { EditorNotesInput } from "@/lib/profiles/editor-notes";

export function FeedbackNotesForm({
  value,
  onChange,
  vote,
  compact = false,
}: {
  value: EditorNotesInput;
  onChange: (next: EditorNotesInput) => void;
  vote?: "accepted" | "rejected" | "positive" | "negative" | null;
  compact?: boolean;
}) {
  const isNegative =
    vote === "rejected" || vote === "negative";

  return (
    <div
      className={
        compact
          ? "mt-2 space-y-2 rounded-md border border-dashed p-3"
          : "space-y-3 rounded-md border p-3"
      }
    >
      <p className="text-xs text-muted-foreground">
        Optional: tell the profile <strong>why</strong> this window is a highlight
        or not. Keywords and sentences are merged with Gemini Flash enrichment
        during training.
      </p>

      <div className="space-y-1.5">
        <label htmlFor="fb-keywords" className="text-xs font-medium">
          Highlight keywords
        </label>
        <input
          id="fb-keywords"
          placeholder="ace, clutch, no way, chat pop-off"
          value={value.keywords}
          onChange={(e) => onChange({ ...value, keywords: e.target.value })}
          className="flex h-8 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs"
        />
      </div>

      <div className="space-y-1.5">
        <label htmlFor="fb-phrases" className="text-xs font-medium">
          Key sentences (one per line)
        </label>
        <textarea
          id="fb-phrases"
          rows={2}
          placeholder={"No way he hit that\nChat is going insane"}
          value={value.phrases}
          onChange={(e) => onChange({ ...value, phrases: e.target.value })}
          className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs"
        />
      </div>

      {isNegative && (
        <div className="space-y-1.5">
          <label htmlFor="fb-anti" className="text-xs font-medium">
            Anti-keywords (why it&apos;s NOT a highlight)
          </label>
          <input
            id="fb-anti"
            placeholder="setup, ads, menu, dead air"
            value={value.antiKeywords}
            onChange={(e) =>
              onChange({ ...value, antiKeywords: e.target.value })
            }
            className="flex h-8 w-full rounded-md border border-input bg-transparent px-3 text-sm shadow-xs"
          />
        </div>
      )}

      <div className="space-y-1.5">
        <label htmlFor="fb-rationale" className="text-xs font-medium">
          {isNegative ? "Why not a highlight?" : "Why is this a highlight?"}
        </label>
        <textarea
          id="fb-rationale"
          rows={2}
          placeholder={
            isNegative
              ? "Slow pacing, no reaction, just walking around..."
              : "Instant chat spike right after the ace, huge audio peak..."
          }
          value={value.rationale}
          onChange={(e) => onChange({ ...value, rationale: e.target.value })}
          className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs"
        />
      </div>

      <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={value.enrichWithGemini}
          onChange={(e) =>
            onChange({ ...value, enrichWithGemini: e.target.checked })
          }
        />
        Enrich with Gemini Flash (extracts structured keywords & signal hints)
      </label>
    </div>
  );
}
