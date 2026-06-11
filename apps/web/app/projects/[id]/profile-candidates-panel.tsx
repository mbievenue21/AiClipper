"use client";

import { useState, useTransition } from "react";
import { Loader2, ThumbsDown, ThumbsUp } from "lucide-react";

import { submitProfileCandidateFeedbackAction } from "@/app/projects/[id]/actions";
import { FeedbackNotesForm } from "@/components/profiles/feedback-notes-form";
import { Button } from "@/components/ui/button";
import type { ProfileScoredCandidate, ProfileSignalBreakdown } from "@/lib/db/schema";
import {
  editorNotesFromInput,
  EMPTY_EDITOR_NOTES_INPUT,
  type EditorNotesInput,
} from "@/lib/profiles/editor-notes";

function formatTimecode(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function BreakdownDetails({ breakdown }: { breakdown: ProfileSignalBreakdown | null }) {
  if (!breakdown) return null;

  const rows = [
    { label: "Audio peak", value: `${(breakdown.audioPeakScore * 100).toFixed(0)}` },
    { label: "Keywords", value: `${(breakdown.keywordScore * 100).toFixed(0)}` },
    { label: "Phrases", value: `${(breakdown.semanticPhraseScore * 100).toFixed(0)}` },
    { label: "Chat burst", value: `${(breakdown.chatBurstScore * 100).toFixed(0)}` },
    { label: "Scene", value: `${(breakdown.sceneScore * 100).toFixed(0)}` },
    { label: "OCR", value: `${(breakdown.ocrScore * 100).toFixed(0)}` },
  ];

  if (breakdown.explanation) {
    rows.push({ label: "Why", value: breakdown.explanation });
  }

  return (
    <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
        Signal breakdown
      </summary>
      <dl className="mt-2 space-y-1 rounded-md bg-muted/40 p-2">
        {rows.map((r) => (
          <div key={r.label} className="grid grid-cols-[7rem_1fr] gap-2">
            <dt className="text-muted-foreground">{r.label}</dt>
            <dd className="text-foreground/90">{r.value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

function CandidateRow({
  projectId,
  profileId,
  candidate,
}: {
  projectId: string;
  profileId: string;
  candidate: ProfileScoredCandidate;
}) {
  const [pending, startTransition] = useTransition();
  const [voted, setVoted] = useState<"accepted" | "rejected" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [showNotes, setShowNotes] = useState(false);
  const [pendingVote, setPendingVote] = useState<"accepted" | "rejected" | null>(
    null,
  );
  const [notesInput, setNotesInput] = useState<EditorNotesInput>(
    EMPTY_EDITOR_NOTES_INPUT,
  );
  const duration = candidate.endSeconds - candidate.startSeconds;
  const breakdown = candidate.signalBreakdownJson ?? null;

  const submit = (vote: "accepted" | "rejected", withNotes: boolean) => {
    startTransition(async () => {
      setMessage(null);
      const result = await submitProfileCandidateFeedbackAction({
        projectId,
        profileId,
        startSeconds: candidate.startSeconds,
        endSeconds: candidate.endSeconds,
        vote,
        breakdown,
        editorNotes: withNotes ? editorNotesFromInput(notesInput) : undefined,
      });
      if (result.ok) {
        setVoted(vote);
        setShowNotes(false);
        setPendingVote(null);
        setNotesInput(EMPTY_EDITOR_NOTES_INPUT);
        setMessage("Saved for profile training.");
      } else {
        setMessage(result.message ?? "Could not save feedback");
      }
    });
  };

  return (
    <div className="rounded-md border border-border/80 p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h4 className="text-sm font-medium leading-tight">
            {candidate.titleSuggestion ||
              `Window @ ${formatTimecode(candidate.startSeconds)}`}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {formatTimecode(candidate.startSeconds)} →{" "}
            {formatTimecode(candidate.endSeconds)} · {duration.toFixed(1)}s
          </p>
          {candidate.explanation && (
            <p className="mt-1 text-xs text-foreground/80">{candidate.explanation}</p>
          )}
        </div>
        <div className="shrink-0 text-right">
          <div className="text-base font-semibold">
            {(candidate.score * 100).toFixed(0)}
          </div>
          <div className="text-[10px] uppercase tracking-wide text-muted-foreground">
            profile
          </div>
        </div>
      </div>
      <BreakdownDetails breakdown={breakdown} />
      <div className="mt-3 border-t border-border/60 pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Train profile:</span>
          <Button
            size="sm"
            variant={voted === "accepted" ? "default" : "outline"}
            disabled={pending || voted !== null}
            onClick={() => {
              setPendingVote("accepted");
              setShowNotes(true);
            }}
          >
            <ThumbsUp className="size-3.5" />
            Good
          </Button>
          <Button
            size="sm"
            variant={voted === "rejected" ? "destructive" : "outline"}
            disabled={pending || voted !== null}
            onClick={() => {
              setPendingVote("rejected");
              setShowNotes(true);
            }}
          >
            <ThumbsDown className="size-3.5" />
            Bad
          </Button>
        </div>
        {showNotes && voted === null && pendingVote && (
          <div className="mt-2 space-y-2">
            <FeedbackNotesForm
              value={notesInput}
              onChange={setNotesInput}
              vote={pendingVote}
              compact
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                disabled={pending}
                onClick={() => submit(pendingVote, true)}
              >
                {pending ? <Loader2 className="size-3.5 animate-spin" /> : "Save"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={pending}
                onClick={() => submit(pendingVote, false)}
              >
                Skip notes
              </Button>
            </div>
          </div>
        )}
        {message && (
          <p className="mt-2 text-xs text-muted-foreground">{message}</p>
        )}
      </div>
    </div>
  );
}

export function ProfileCandidatesPanel({
  projectId,
  profileId,
  candidates,
  maxVisible = 12,
}: {
  projectId: string;
  profileId: string;
  candidates: ProfileScoredCandidate[];
  maxVisible?: number;
}) {
  if (candidates.length === 0) return null;

  const visible = candidates.slice(0, maxVisible);

  return (
    <div className="space-y-3">
      {visible.map((c) => (
        <CandidateRow
          key={c.id}
          projectId={projectId}
          profileId={profileId}
          candidate={c}
        />
      ))}
      {candidates.length > maxVisible && (
        <p className="text-xs text-muted-foreground">
          Showing top {maxVisible} of {candidates.length} profile-scored windows.
        </p>
      )}
    </div>
  );
}
