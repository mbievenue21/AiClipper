"use client";

import { useState, useTransition } from "react";
import { ThumbsDown, ThumbsUp } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import type { ClipSignalVotes, HighlightReason } from "@/lib/db/schema";

import { submitClipFeedbackAction } from "./actions";

type SignalKey = keyof ClipSignalVotes;

const SIGNALS: { key: SignalKey; label: string; scoreKey: string }[] = [
  { key: "visual", label: "Visual", scoreKey: "visual" },
  { key: "audio", label: "Audio", scoreKey: "audio" },
  { key: "chat", label: "Chat", scoreKey: "chat" },
  { key: "transcript", label: "Transcript", scoreKey: "transcript" },
  { key: "fusion", label: "Fusion", scoreKey: "fusion" },
  { key: "gemini", label: "Gemini pick", scoreKey: "gemini" },
];

function scoreFor(reason: HighlightReason | null, key: string): number | null {
  if (!reason) return null;
  const s = reason.scores;
  if (key === "visual") return s?.visual ?? reason.visualScore ?? null;
  if (key === "audio") return s?.audio ?? reason.audioScore ?? null;
  if (key === "chat") return s?.chat ?? reason.chatScore ?? null;
  if (key === "transcript") return s?.transcript ?? null;
  if (key === "fusion") return s?.fusion ?? reason.fusionScore ?? null;
  if (key === "gemini") return reason.llmScore ?? null;
  return null;
}

export function ClipFeedbackPanel({
  projectId,
  clipId,
  highlightId,
  highlightStart,
  highlightEnd,
  sourceStart,
  sourceEnd,
  reason,
  learnedPreRoll,
  feedbackCount,
}: {
  projectId: string;
  clipId: string;
  highlightId: string;
  highlightStart: number;
  highlightEnd: number;
  sourceStart: number | null;
  sourceEnd: number | null;
  reason: HighlightReason | null;
  learnedPreRoll: number;
  feedbackCount: number;
}) {
  const [pending, startTransition] = useTransition();
  const [overall, setOverall] = useState<"up" | "down" | null>(null);
  const [signals, setSignals] = useState<ClipSignalVotes>({});
  const [submitted, setSubmitted] = useState(false);

  const toggleSignal = (key: SignalKey, vote: "up" | "down") => {
    setSignals((prev) => ({
      ...prev,
      [key]: prev[key] === vote ? "skip" : vote,
    }));
  };

  const submit = (overallVote?: "up" | "down") => {
    startTransition(async () => {
      const res = await submitClipFeedbackAction({
        projectId,
        clipId,
        highlightId,
        overallVote: overallVote ?? overall ?? undefined,
        signalVotes: signals,
        highlightStart,
        highlightEnd,
        sourceStart,
        sourceEnd,
        reason,
      });
      if (res.ok) {
        setSubmitted(true);
        toast.success(
          res.learnedPreRoll != null
            ? `Thanks — learned pre-roll now ~${res.learnedPreRoll}s`
            : "Feedback saved — ranking weights updated",
        );
      } else {
        toast.error(res.message);
      }
    });
  };

  if (submitted) {
    return (
      <p className="text-xs text-muted-foreground">
        Feedback recorded. Learned pre-roll default: {learnedPreRoll}s (
        {feedbackCount + 1} samples).
      </p>
    );
  }

  return (
    <div className="rounded-md border border-dashed bg-muted/20 p-3 text-xs">
      <p className="mb-2 font-medium">Rate this clip — trains future ranking</p>
      <div className="mb-3 flex gap-2">
        <Button
          type="button"
          size="sm"
          variant={overall === "up" ? "default" : "outline"}
          disabled={pending}
          onClick={() => {
            setOverall("up");
            submit("up");
          }}
        >
          <ThumbsUp className="mr-1 size-3.5" />
          Good clip
        </Button>
        <Button
          type="button"
          size="sm"
          variant={overall === "down" ? "destructive" : "outline"}
          disabled={pending}
          onClick={() => {
            setOverall("down");
            submit("down");
          }}
        >
          <ThumbsDown className="mr-1 size-3.5" />
          Missed the mark
        </Button>
      </div>
      <p className="mb-2 text-muted-foreground">
        Which signals drove this pick? (optional, refines weights)
      </p>
      <div className="space-y-1.5">
        {SIGNALS.map(({ key, label, scoreKey }) => {
          const sc = scoreFor(reason, scoreKey);
          return (
            <div
              key={key}
              className="flex items-center justify-between gap-2 rounded border bg-background/60 px-2 py-1"
            >
              <span>
                {label}
                {sc != null && (
                  <span className="ml-1 font-mono text-muted-foreground">
                    ({sc.toFixed(2)})
                  </span>
                )}
              </span>
              <span className="flex gap-1">
                <Button
                  type="button"
                  size="icon"
                  variant={signals[key] === "up" ? "default" : "ghost"}
                  className="size-7"
                  disabled={pending}
                  onClick={() => toggleSignal(key, "up")}
                >
                  <ThumbsUp className="size-3" />
                </Button>
                <Button
                  type="button"
                  size="icon"
                  variant={signals[key] === "down" ? "destructive" : "ghost"}
                  className="size-7"
                  disabled={pending}
                  onClick={() => toggleSignal(key, "down")}
                >
                  <ThumbsDown className="size-3" />
                </Button>
              </span>
            </div>
          );
        })}
      </div>
      <Button
        type="button"
        size="sm"
        className="mt-3"
        variant="secondary"
        disabled={pending || Object.keys(signals).length === 0}
        onClick={() => submit()}
      >
        Save signal feedback
      </Button>
    </div>
  );
}
