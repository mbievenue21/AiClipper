"use client";

import { useState, useTransition } from "react";
import { Loader2, ThumbsDown, ThumbsUp } from "lucide-react";

import { submitHighlightFeedbackAction } from "@/app/projects/[id]/actions";
import { FeedbackNotesForm } from "@/components/profiles/feedback-notes-form";
import { Button } from "@/components/ui/button";
import type { HighlightReason } from "@/lib/db/schema";
import {
  editorNotesFromInput,
  EMPTY_EDITOR_NOTES_INPUT,
  type EditorNotesInput,
} from "@/lib/profiles/editor-notes";

export function HighlightFeedbackPanel({
  projectId,
  highlightId,
  startSeconds,
  endSeconds,
  profileId,
  reason,
  currentStatus,
}: {
  projectId: string;
  highlightId: string;
  startSeconds: number;
  endSeconds: number;
  profileId?: string | null;
  reason?: HighlightReason | null;
  currentStatus?: string;
}) {
  const [pending, startTransition] = useTransition();
  const [message, setMessage] = useState<string | null>(null);
  const [showNotes, setShowNotes] = useState(false);
  const [pendingVote, setPendingVote] = useState<"accepted" | "rejected" | null>(
    null,
  );
  const [notesInput, setNotesInput] = useState<EditorNotesInput>(
    EMPTY_EDITOR_NOTES_INPUT,
  );
  const [voted, setVoted] = useState(
    currentStatus === "approved"
      ? "accepted"
      : currentStatus === "rejected"
        ? "rejected"
        : null,
  );

  const submit = (vote: "accepted" | "rejected") => {
    if (!showNotes) {
      setPendingVote(vote);
      setShowNotes(true);
      return;
    }

    startTransition(async () => {
      setMessage(null);
      const result = await submitHighlightFeedbackAction({
        projectId,
        highlightId,
        profileId,
        startSeconds,
        endSeconds,
        vote,
        reason,
        editorNotes: editorNotesFromInput({
          ...notesInput,
          enrichWithGemini: notesInput.enrichWithGemini,
        }),
      });
      if (result.ok) {
        setVoted(vote);
        setShowNotes(false);
        setPendingVote(null);
        setNotesInput(EMPTY_EDITOR_NOTES_INPUT);
        setMessage(
          vote === "accepted"
            ? "Saved — profile will learn from your notes."
            : "Saved — profile will avoid similar windows.",
        );
      } else {
        setMessage(result.message ?? "Could not save feedback");
      }
    });
  };

  if (!profileId) return null;

  return (
    <div className="mt-3 border-t border-border/60 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Train profile:</span>
        <Button
          size="sm"
          variant={voted === "accepted" ? "default" : "outline"}
          disabled={pending || voted !== null}
          onClick={() => submit("accepted")}
        >
          {pending && pendingVote === "accepted" ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <ThumbsUp className="size-3.5" />
          )}
          Good clip
        </Button>
        <Button
          size="sm"
          variant={voted === "rejected" ? "destructive" : "outline"}
          disabled={pending || voted !== null}
          onClick={() => submit("rejected")}
        >
          <ThumbsDown className="size-3.5" />
          Bad clip
        </Button>
        {voted && (
          <span className="text-xs capitalize text-muted-foreground">{voted}</span>
        )}
      </div>

      {showNotes && voted === null && (
        <div className="mt-3 space-y-2">
          <FeedbackNotesForm
            value={notesInput}
            onChange={setNotesInput}
            vote={pendingVote}
            compact
          />
          <div className="flex gap-2">
            <Button
              size="sm"
              disabled={pending || !pendingVote}
              onClick={() => pendingVote && submit(pendingVote)}
            >
              {pending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                "Save with notes"
              )}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={pending}
              onClick={() => {
                if (pendingVote) {
                  startTransition(async () => {
                    const result = await submitHighlightFeedbackAction({
                      projectId,
                      highlightId,
                      profileId,
                      startSeconds,
                      endSeconds,
                      vote: pendingVote,
                      reason,
                    });
                    if (result.ok) {
                      setVoted(pendingVote);
                      setShowNotes(false);
                      setPendingVote(null);
                    }
                  });
                }
              }}
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
  );
}
